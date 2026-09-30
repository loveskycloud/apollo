use std::fmt::Write as _;
use std::net::IpAddr;
use std::time::Duration;

use clap::{CommandFactory as _, Subcommand};
use itertools::Itertools as _;
use re_data_source::{AuthErrorHandler, LogDataSource};
use re_log_channel::{DataSourceMessage, LogReceiver, LogReceiverSet, SmartMessagePayload};
#[cfg(feature = "web_viewer")]
use re_sdk::web_viewer::WebViewerConfig;
use tokio::runtime::Runtime;

#[cfg(feature = "auth")]
use super::auth::AuthCommands;
use crate::CallSource;
#[cfg(feature = "analytics")]
use crate::commands::AnalyticsCommands;
use crate::commands::DownloadCommand;
#[cfg(feature = "importers")]
use crate::commands::McapCommands;
use crate::commands::RrdCommands;

// ---

#[cfg(all(feature = "server", feature = "web_viewer"))]
#[path = "convert_record.rs"]
mod convert_record;

#[cfg(all(feature = "server", feature = "web_viewer"))]
#[path = "browser_record.rs"]
mod browser_record;

const LONG_ABOUT: &str = r#"
The Rerun command-line interface:
* Spawn viewers to visualize Rerun recordings and other supported formats.
* Start a gRPC server to share recordings over the network, on native or web.
* Inspect, edit and filter Rerun recordings.
"#;

// Place the important help _last_, to make it most visible in the terminal.
const ENVIRONMENT_VARIABLES_AND_EXAMPLES: &str = r#"
Environment variables:
    RERUN_CHUNK_MAX_BYTES     Maximum chunk size threshold for the compactor.
    RERUN_CHUNK_MAX_ROWS      Maximum chunk row count threshold for the compactor (sorted chunks).
    RERUN_CHUNK_MAX_ROWS_IF_UNSORTED
                              Maximum chunk row count threshold for the compactor (unsorted chunks).
    RERUN_SHADER_PATH         The search path for shader/shader-imports. Only available in developer builds.
    RERUN_TRACK_ALLOCATIONS   Track memory allocations to diagnose memory leaks in the viewer.
                              WARNING: slows down the viewer by a lot!
    RERUN_MAPBOX_ACCESS_TOKEN The Mapbox access token to use the Mapbox-provided backgrounds in the map view.
    RUST_LOG                  Change the log level of the viewer, e.g. `RUST_LOG=debug`.
    WGPU_BACKEND              Overwrites the graphics backend used, must be one of `vulkan`, `metal` or `gl`.
                              Default is `vulkan` everywhere except on Mac where we use `metal`. What is
                              supported depends on your OS.
    WGPU_POWER_PREF           Overwrites the power setting used for choosing a graphics adapter, must be `high`
                              or `low`. (Default is `high`)


Examples:
    Open a Rerun Viewer that listens for incoming SDK connections:
        rerun

    Load some files and show them in the Rerun Viewer:
        rerun recording.rrd mesh.obj image.png https://example.com/recording.rrd

    Open an .rrd file and stream it to a Web Viewer:
        rerun recording.rrd --web-viewer

    Host a Rerun gRPC server which listens for incoming connections from the logging SDK, buffer the log messages, and serve the results:
        rerun --serve-web

    Host a Rerun Server which serves a recording from a file over gRPC to any connecting Rerun Viewers:
        rerun --serve-web recording.rrd

    Host a Rerun gRPC server without spawning a Viewer:
        rerun --serve-grpc

    Spawn a Viewer without also hosting a gRPC server:
        rerun --connect

    Connect to a Rerun Server:
        rerun rerun+http://localhost:9877/proxy

    Listen for incoming gRPC connections from the logging SDK and stream the results to disk:
        rerun --save new_recording.rrd
"#;

/// Port argument that accepts either a port number or `auto`.
///
/// `auto` will use the default port, but find a free one if it's already in use.
#[derive(Debug, Clone)]
enum PortArg {
    Port(u16),
    Auto,
}

impl PortArg {
    fn port(&self) -> u16 {
        match self {
            Self::Port(port) => *port,
            Self::Auto => 9876,
        }
    }

    fn is_auto(&self) -> bool {
        matches!(self, Self::Auto)
    }
}

impl std::fmt::Display for PortArg {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Port(port) => write!(f, "{port}"),
            Self::Auto => write!(f, "auto"),
        }
    }
}

impl std::str::FromStr for PortArg {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        if s.eq_ignore_ascii_case("auto") {
            Ok(Self::Auto)
        } else {
            s.parse::<u16>()
                .map(Self::Port)
                .map_err(|err| format!("invalid port: {err}"))
        }
    }
}

#[derive(Debug, clap::Parser)]
#[clap(
    long_about = LONG_ABOUT,
    // Place most of the help last, as that is most visible in the terminal.
    after_long_help = ENVIRONMENT_VARIABLES_AND_EXAMPLES
)]
struct Args {
    // Note: arguments are sorted lexicographically for nicer `--help` message.
    //
    // We also use `long_help` on some arguments for more compact formatting.
    //
    #[command(subcommand)]
    command: Option<Command>,

    /// What bind address IP to use.
    ///
    /// `::` will listen on all interfaces, IPv6 and IPv4.
    #[clap(long, default_value = "0.0.0.0")]
    bind: IpAddr,

    #[clap(
        long,
        long_help = r"An upper limit on how much memory the Rerun Viewer should use.
When this limit is reached, Rerun will drop the oldest data.
Example: `16GB` or `50%` (of system total).
You can also set this in the settings panel."
    )]
    memory_limit: Option<String>,

    #[clap(
        long,
        default_value = "1GiB",
        long_help = r"An upper limit on how much memory the gRPC server (`--serve-web`) should use.
The server buffers log messages for the benefit of late-arriving viewers.
When this limit is reached, Rerun will drop the oldest data.
Example: `16GB` or `50%` (of system total)."
    )]
    server_memory_limit: String,

    /// If true, play back the most recent data first when new clients connect.
    #[clap(long)]
    newest_first: bool,

    /// Additional origin patterns allowed to make CORS requests to the gRPC server.
    ///
    /// Use this when hosting a custom viewer on a different domain.
    /// Patterns are matched against the full Origin header (e.g. `https://example.com:8080`),
    /// using glob-style matching where `*` matches any sequence of characters.
    /// Can be specified multiple times.
    ///
    /// Examples:
    ///   `--cors-allow-origin "https://*.example.com"`
    ///   `--cors-allow-origin "https://example.com:8080"`
    ///   `--cors-allow-origin "https://example.com:*"`
    #[clap(long)]
    cors_allow_origin: Vec<String>,

    #[clap(
        long,
        default_value_t = true,
        long_help = r"Whether the Rerun Viewer should persist the state of the viewer to disk.
When persisted, the state will be stored at the following locations:
- Linux: `/home/UserName/.local/share/rerun`
- macOS: `/Users/UserName/Library/Application Support/rerun`
- Windows: `C:\Users\UserName\AppData\Roaming\rerun`"
    )]
    persist_state: bool,

    /// What port do we listen to for SDKs to connect to over gRPC.
    ///
    /// Use `auto` to always start a new viewer with a free port if the default is taken.
    // Default is `re_grpc_server::DEFAULT_SERVER_PORT`, can't use symbollically if `server` feature is disabled
    #[clap(long, default_value_t = PortArg::Port(9876))]
    port: PortArg,

    /// Alias for `--port auto`. Always start a new viewer.
    ///
    /// If the port is already in use, a free port will be picked automatically.
    #[clap(long, conflicts_with = "port")]
    new: bool,

    /// Start with the puffin profiler running.
    #[clap(long)]
    profile: bool,

    /// Stream incoming log events to an .rrd file at the given path.
    #[clap(long)]
    save: Option<String>,

    /// Take a screenshot of the app and quit.
    /// We use this to generate screenshots of our examples.
    /// Useful together with `--window-size`.
    #[clap(long)]
    screenshot_to: Option<std::path::PathBuf>,

    /// This will host a web-viewer over HTTP, and a gRPC server,
    /// unless one or more URIs are provided that can be viewed directly in the web viewer.
    ///
    /// If started, the web server will act like a proxy, listening for incoming connections from
    /// logging SDKs, and forwarding it to Rerun viewers.
    //
    // TODO(andreas): The Rust/Python APIs deprecated `serve_web` and instead encourage separate usage of `rec.serve_grpc()` + `rerun::serve_web_viewer()` instead.
    // It's worth considering doing the same here.
    #[clap(long)]
    serve_web: bool,

    /// This will host a gRPC server.
    ///
    /// The server will act like a proxy, listening for incoming connections from
    /// logging SDKs, and forwarding it to Rerun viewers.
    #[clap(long)]
    serve_grpc: bool,

    /// Do not attempt to start a new server, instead try to connect to an existing one.
    ///
    /// Optionally accepts a URL to a gRPC server.
    ///
    /// The scheme must be one of `rerun://`, `rerun+http://`, or `rerun+https://`,
    /// and the pathname must be `/proxy`.
    ///
    /// The default is `rerun+http://127.0.0.1:9876/proxy`.
    #[clap(long)]
    #[expect(clippy::option_option)] // Tri-state: none, --connect, --connect <url>.
    connect: Option<Option<String>>,

    /// This is a hint that we expect a recording to stream in very soon.
    ///
    /// This is set by the `spawn()` method in our logging SDK.
    ///
    /// The viewer will respond by fading in the welcome screen,
    /// instead of showing it directly.
    /// This ensures that it won't blink for a few frames before switching to the recording.
    #[clap(long)]
    expect_data_soon: bool,

    /// The number of compute threads to use.
    ///
    /// If zero, the same number of threads as the number of cores will be used.
    /// If negative, will use that much fewer threads than cores.
    ///
    /// Rerun will still use some additional threads for I/O.
    #[clap(
        long,
        short = 'j',
        default_value = "-2", // save some CPU for the main thread and the rest of the users system
    )]
    threads: i32,

    #[clap(long_help = r"Any combination of:
- A gRPC url to a Rerun server
- A path to a Rerun .rrd recording
- A path to a Rerun .rbl blueprint
- An HTTP(S) URL to an .rrd or .rbl file to load
- A path to an image or mesh, or any other file that Rerun can load (see https://www.rerun.io/docs/concepts/logging-and-ingestion/importers/overview)

If no arguments are given, a server will be hosted which a Rerun SDK can connect to.")]
    url_or_paths: Vec<String>,

    /// Print version and quit.
    #[clap(long)]
    version: bool,

    /// Start the viewer in the browser (instead of locally).
    ///
    /// Requires Rerun to have been compiled with the `web_viewer` feature.
    ///
    /// This implies `--serve-web`.
    #[clap(long)]
    web_viewer: bool,

    /// What port do we listen to for hosting the web viewer over HTTP.
    /// A port of 0 will pick a random port.
    // Default is `re_web_viewer_server::DEFAULT_WEB_VIEWER_SERVER_PORT`, can't use symbollically if `web_viewer` feature is disabled
    #[clap(long, default_value_t = 9090)]
    web_viewer_port: u16,

    /// Hide the normal Rerun welcome screen.
    #[clap(long)]
    hide_welcome_screen: bool,

    /// Detach the native Rerun Viewer process from the invoking process.
    ///
    /// Ignored for any command that doesn't spawn a viewer.
    #[clap(long)]
    detach_process: bool,

    /// Marks the relaunched child of a detached Rerun Viewer.
    #[clap(long, hide = true)]
    detached_process_child: bool,

    /// Run the viewer in headless mode (no OS window).
    ///
    /// The viewer is driven by an offscreen `egui_kittest` harness, while the
    /// gRPC server keeps running so SDK clients can still log data and request
    /// screenshots via `save_screenshot`.
    #[clap(long)]
    headless: bool,

    /// Run the viewer in the context of an integration test.
    ///
    /// This isolates the viewer from the developer's environment so tests are reproducible:
    /// it does not read or write persisted viewer state (blueprints, panel layout, recent
    /// servers), does not use stored redap credentials, and does not record analytics.
    ///
    /// Intended to be used together with `--headless` when driving the viewer over
    /// `egui_inspection` from an integration test.
    ///
    /// Hidden from `--help` and the generated CLI manual: it's a testing-only flag, not part of
    /// the public interface.
    #[clap(long, hide = true)]
    integration_test: bool,

    /// Set the screen resolution (in logical points), e.g. "1920x1080".
    /// Useful together with `--screenshot-to`.
    #[clap(long)]
    window_size: Option<String>,

    /// Override the default graphics backend and for a specific one instead.
    ///
    /// When using `--web-viewer` this should be one of: `webgpu`, `webgl`.
    ///
    /// When starting a native viewer instead this should be one of:
    ///
    /// * `vulkan` (Linux & Windows only)
    ///
    /// * `gl` (Linux & Windows only)
    ///
    /// * `metal` (macOS only)
    //
    // Note that we don't compile with DX12 right now, but we could (we don't since this adds permutation and wgpu still has some issues with it).
    // GL could be enabled on MacOS via `angle` but given prior issues with ANGLE this seems to be a bad idea!
    #[clap(long)]
    renderer: Option<String>,

    /// Overwrites hardware acceleration option for video decoding.
    ///
    /// By default uses the last provided setting, which is `auto` if never configured.
    ///
    /// Depending on the decoder backend, these settings are merely hints and may be ignored.
    /// However, they can be useful in some situations to work around issues.
    ///
    /// Possible values:
    ///
    /// * `auto`
    ///   May use hardware acceleration if available and compatible with the codec.
    ///
    /// * `prefer_software`
    ///   Should use a software decoder even if hardware acceleration is available.
    ///   If no software decoder is present, this may cause decoding to fail.
    ///
    /// * `prefer_hardware`
    ///   Should use a hardware decoder.
    ///   If no hardware decoder is present, this may cause decoding to fail.
    #[clap(long, verbatim_doc_comment)]
    video_decoder: Option<String>,

    // ----------------------------------------------------------------------------
    // Debug-options:
    /// Ingest data and then quit once the goodbye message has been received.
    ///
    /// Used for testing together with `RERUN_PANIC_ON_WARN=1`.
    ///
    /// Fails if no messages are received, or if no messages are received within a dozen or so seconds.
    #[clap(long)]
    test_receive: bool,
}

impl Args {
    fn generate_markdown_manual() -> String {
        let mut out = String::new();

        fn generate_arg_doc(arg: &clap::Arg) -> String {
            let mut names = Vec::new();
            if let Some(short) = arg.get_short() {
                names.push(format!("-{short}"));
            }
            if let Some(long) = arg.get_long() {
                names.push(format!("--{long}"));
            }

            let values = arg.get_value_names().map_or_else(String::new, |values| {
                values
                    .iter()
                    .map(|v| format!("<{v}>"))
                    .collect_vec()
                    .join(", ")
            });

            let help = if let Some(help) = arg.get_long_help() {
                Some(
                    help.to_string()
                        .lines()
                        .map(|line| format!("> {line}").trim().to_owned())
                        .collect_vec()
                        .join("\n"),
                )
            } else {
                arg.get_help().map(|help| {
                    if help.to_string().ends_with('?') {
                        format!("> {help}")
                    } else {
                        format!("> {help}.")
                    }
                    .trim()
                    .to_owned()
                })
            };

            let rendered = if names.is_empty() {
                format!("* `{values}`")
            } else {
                format!("* `{} {values}`", names.join(", "))
            }
            .trim()
            .to_owned();

            let rendered = if let Some(help) = help {
                format!("{rendered}\n{help}")
            } else {
                rendered
            }
            .trim()
            .to_owned();

            let defaults = arg.get_default_values();
            if defaults.is_empty() {
                rendered
            } else {
                let defaults = defaults
                    .iter()
                    .map(|v| format!("`{}`", v.to_string_lossy().trim()))
                    .collect_vec()
                    .join(", ");
                format!("{rendered}\n>\n> [Default: {defaults}]")
                    .trim()
                    .to_owned()
            }
        }

        fn generate_markdown_manual(
            full_name: Vec<String>,
            out: &mut String,
            cmd: &mut clap::Command,
        ) {
            let name = cmd.get_name();

            if name == "help" {
                return;
            }

            let any_subcommands = cmd
                .get_subcommands()
                .any(|cmd| cmd.get_name() != "help" && !cmd.is_hide_set());
            let any_positional_args = cmd.get_arguments().any(|arg| arg.is_positional());
            let any_floating_args = cmd.get_arguments().any(|arg| {
                !arg.is_positional() && !arg.is_hide_set() && arg.get_long() != Some("help")
            });

            let full_name =
                std::iter::chain(full_name, std::iter::once(name.to_owned())).collect_vec();

            if !any_positional_args && !any_floating_args && !any_subcommands {
                return;
            }

            // E.g. "## rerun analytics"
            let header = format!("{} {}", "##", full_name.join(" "))
                .trim()
                .to_owned();

            // E.g. "**Usage**: `rerun [OPTIONS] [URL_OR_PATHS]… [COMMAND]`"
            let usage = {
                let usage = cmd.render_usage().to_string();
                let (_, usage) = usage.split_at(7);
                let full_name = {
                    let mut full_name = full_name.clone();
                    _ = full_name.pop();
                    full_name
                };

                let mut rendered = String::new();
                if let Some(about) = cmd.get_long_about() {
                    write!(rendered, "{about}\n\n").ok();
                } else if let Some(about) = cmd.get_about() {
                    write!(rendered, "{about}.\n\n").ok();
                }
                rendered += format!("**Usage**: `{} {usage}`", full_name.join(" ")).trim();

                rendered
            };

            // E.g.:
            // """
            // **Commands**
            //
            // * `analytics`: Configure the behavior of our analytics
            // * `rrd`: Manipulate the contents of .rrd and .rbl files
            // * `reset`: Reset the memory of the Rerun Viewer
            // """
            let commands = any_subcommands.then(|| {
                let commands = cmd
                    .get_subcommands_mut()
                    .filter(|cmd| cmd.get_name() != "help" && !cmd.is_hide_set())
                    .map(|cmd| {
                        let name = cmd.get_name().to_owned();
                        let help = cmd.render_help().to_string();
                        let help = help.split_once('\n').map_or("", |(help, _)| help).trim();
                        // E.g. "`analytics`:  Configure the behavior of our analytics"
                        format!("* `{name}`: {help}.")
                    })
                    .collect_vec()
                    .join("\n");

                format!("**Commands**\n\n{commands}")
            });

            // E.g.:
            // """
            // **Arguments**
            //
            // `[URL_OR_PATHS]…`
            // > Any combination of:
            // > - A gRPC url to a Rerun server
            // > - A path to a Rerun .rrd recording
            // > - A path to a Rerun .rbl blueprint
            // > - An HTTP(S) URL to an .rrd or .rbl file to load
            // > - A path to an image or mesh, or any other file that Rerun can load (see https://www.rerun.io/docs/concepts/logging-and-ingestion/importers/overview)
            // >
            // > If no arguments are given, a server will be hosted which a Rerun SDK can connect to.
            // """
            let positionals = any_positional_args.then(|| {
                let arguments = cmd
                    .get_arguments()
                    .filter(|arg| arg.is_positional())
                    .map(generate_arg_doc)
                    .collect_vec()
                    .join("\n\n");

                format!("**Arguments**\n\n{arguments}")
            });

            // E.g.:
            // """
            // **Options**
            //
            // `--bind <BIND>`
            // > What bind address IP to use.
            // >
            // > [default: ::]
            // """
            let floatings = any_floating_args.then(|| {
                let options = cmd
                    .get_arguments()
                    .filter(|arg| {
                        !arg.is_positional() && !arg.is_hide_set() && arg.get_long() != Some("help")
                    })
                    .map(generate_arg_doc)
                    .collect_vec()
                    .join("\n\n");

                format!("**Options**\n\n{options}")
            });

            *out += &[Some(header), Some(usage), commands, positionals, floatings]
                .into_iter()
                .flatten()
                .collect_vec()
                .join("\n\n");

            *out += "\n\n";

            for cmd in cmd.get_subcommands_mut() {
                if cmd.is_hide_set() {
                    continue;
                }
                generate_markdown_manual(full_name.clone(), out, cmd);
            }
        }

        generate_markdown_manual(Vec::new(), &mut out, &mut Self::command());

        out.trim().replace("...", "…") // NOLINT
    }
}

// Commands sorted alphabetically:
#[derive(Debug, Clone, Subcommand)]
enum Command {
    /// Configure the behavior of our analytics.
    #[cfg(feature = "analytics")]
    #[command(subcommand)]
    Analytics(AnalyticsCommands),

    /// Authentication with the redap.
    #[cfg(feature = "auth")]
    #[command(subcommand)]
    Auth(AuthCommands),

    /// Download recordings and save them as .rrd files.
    ///
    /// Supports downloading from Rerun Hub as well as any other supported URI.
    Download(DownloadCommand),

    /// Generates the Rerun CLI manual (markdown).
    ///
    /// Example: `rerun man > docs/content/reference/cli.md`
    #[command(name = "man")]
    Manual,

    #[cfg(feature = "importers")]
    #[command(subcommand)]
    Mcap(McapCommands),

    /// Run an MCP server that controls a running Rerun Viewer.
    ///
    /// See the [mcp docs](https://rerun.io/docs/reference/viewer/mcp) for more info about using
    /// `rerun viewer-mcp`.
    ///
    /// Use the following to commands to register the mcp with your agent:
    /// - `claude mcp add rerun -- rerun viewer-mcp`
    /// - `codex mcp add rerun -- rerun viewer-mcp`
    ///
    /// Or add a mcp.json with the following content:
    /// ```json
    /// {
    ///   "mcpServers": {
    ///     "rerun": {
    ///       "command": "rerun",
    ///       "args": ["viewer-mcp"],
    ///     }
    ///   }
    /// }
    /// ```
    #[cfg(feature = "native_viewer")]
    #[command(name = "viewer-mcp")]
    ViewerMcp,

    /// Reset the memory of the Rerun Viewer.
    ///
    /// Only run this if you're having trouble with the Viewer,
    /// e.g. if it is crashing on startup.
    ///
    /// Rerun will forget all blueprints, as well as the native window's size, position and scale factor.
    #[cfg(feature = "native_viewer")]
    Reset,

    #[command(subcommand)]
    Rrd(RrdCommands),

    /// In-memory Rerun data server
    #[cfg(feature = "oss_server")]
    #[command(name = "server")]
    Server(re_server::Args),
}

/// Run the Rerun application and return an exit code.
///
/// This is used by the `rerun` binary and the Rerun Python SDK via `python -m rerun [args…]`.
///
/// This installs crash panic and signal handlers that sends analytics on panics and signals.
/// These crash reports includes a stacktrace. We make sure the file paths in the stacktrace
/// don't include and sensitive parts of the path (like user names), but the function names
/// are all included, which means you should ONLY call `run` from a function with
/// a non-sensitive name.
///
/// In the future we plan to support installing user plugins (that act like callbacks),
/// and when we do we must make sure to give users an easy way to opt-out of the
/// crash callstacks, as those could include the file and function names of user code.
//
// It would be nice to use [`std::process::ExitCode`] here but
// then there's no good way to get back at the exit code from python
pub fn run<I, T>(
    main_thread_token: crate::MainThreadToken,
    build_info: re_build_info::BuildInfo,
    call_source: CallSource,
    args: I,
) -> anyhow::Result<u8>
where
    I: IntoIterator<Item = T>,
    T: Into<std::ffi::OsString> + Clone,
{
    #[cfg(feature = "native_viewer")]
    re_memory::accounting_allocator::turn_on_tracking_if_env_var(
        re_viewer::env_vars::RERUN_TRACK_ALLOCATIONS,
    );

    #[cfg(not(target_arch = "wasm32"))]
    if cfg!(feature = "perf_telemetry") && re_log::env_var_is_truthy("TELEMETRY_ENABLED") {
        eprintln!("Disabling crash handler because of perf_telemetry/TELEMETRY_ENABLED"); // Ask Clement why
    } else {
        re_crash_handler::install_crash_handlers(build_info.clone());
    }

    // There is always value in setting this, even if `re_perf_telemetry` is disabled. For example,
    // the Rerun versioning headers will automatically pick it up.
    //
    // Safety: anything touching the env is unsafe, tis what it is.
    #[expect(unsafe_code)]
    unsafe {
        std::env::set_var("OTEL_SERVICE_NAME", "rerun");
    }

    let raw_args = args.into_iter().map(Into::into).collect::<Vec<_>>();

    use clap::Parser as _;
    let mut args = Args::parse_from(raw_args.iter());

    #[cfg(feature = "native_viewer")]
    if should_relaunch_detached(&args) {
        relaunch_detached(&raw_args)?;
        return Ok(0);
    }

    #[cfg(feature = "analytics")]
    if !args.integration_test {
        record_cli_command_analytics(&args);
    }

    initialize_thread_pool(args.threads);

    if args.web_viewer {
        args.serve_web = true;
    }

    if args.version {
        println!("{build_info}");
        #[cfg(feature = "video")]
        println!(
            "Video features: {}",
            re_video::enabled_features().iter().join(" ")
        );
        #[cfg(not(feature = "video"))]
        println!("Video features: (video support disabled in this build)");
        return Ok(0);
    }

    #[cfg(feature = "native_viewer")]
    let profiler = run_profiler(&args);

    // We don't want the runtime to run on the main thread, as we need that one for our UI.
    // So we can't call `block_on` anywhere in the entrypoint - we must call `tokio::spawn`
    // and synchronize the result using some other means instead.
    let tokio_runtime = initialize_tokio_runtime(args.threads)?;
    let _tokio_guard = tokio_runtime.enter();

    let res = if let Some(command) = args.command {
        match command {
            #[cfg(feature = "auth")]
            Command::Auth(cmd) => cmd.run(tokio_runtime.handle()).map_err(Into::into),

            #[cfg(feature = "analytics")]
            Command::Analytics(analytics) => analytics.run().map_err(Into::into),

            Command::Download(cmd) => cmd.run(tokio_runtime.handle()),

            Command::Manual => {
                let man = Args::generate_markdown_manual();
                let web_header = unindent::unindent(
                    "\
                    ---
                    title: ⌨️ CLI manual
                    order: 1150
                    ---

                    <!-- DO NOT EDIT! This file was auto-generated by `pixi run man`. -->\
                    ",
                );
                println!("{web_header}\n\n{man}");
                Ok(())
            }

            #[cfg(feature = "importers")]
            Command::Mcap(mcap) => mcap.run(),

            #[cfg(feature = "native_viewer")]
            Command::ViewerMcp => tokio_runtime.block_on(re_viewer_mcp::serve()),

            #[cfg(feature = "native_viewer")]
            Command::Reset => re_viewer::reset_viewer_persistence(),

            Command::Rrd(rrd) => rrd.run(),

            #[cfg(feature = "oss_server")]
            Command::Server(server) => tokio_runtime.block_on(server.run_async()),
        }
    } else {
        #[cfg(all(not(target_arch = "wasm32"), feature = "perf_telemetry"))]
        let mut _telemetry = {
            // NOTE: We're just parsing the environment, hence the `vec![]` for CLI flags.
            use re_perf_telemetry::external::clap::Parser as _;
            let args = re_perf_telemetry::TelemetryArgs::parse_from::<_, String>(vec![]);

            // Remember: telemetry must be init in a Tokio context.
            tokio_runtime.block_on(async {
                re_perf_telemetry::Telemetry::init(
                    args,
                    re_perf_telemetry::TelemetryDropBehavior::Shutdown,
                )
                // Perf telemetry is a developer tool, it's not compiled into final user builds.
                .expect("could not start perf telemetry")
            })

            // TODO(tokio-rs/tracing#3239): The viewer will crash on exit because of what appears
            // to be a design flaw in `tracing-subscriber`'s shutdown implementation, specifically
            // it assumes that all the relevant thread-local state will be dropped in the proper
            // order, when really it won't and there's no way to guarantee that.
            // See <https://github.com/tokio-rs/tracing/issues/3239>.
            //
            // What happens in practice will depend on what you and all your dependencies are
            // doing. This problem has been seen before specifically for egui apps [1], but really
            // it has nothing to do with egui per se.
            // [1]: <https://github.com/smol-rs/polling/issues/231>
            //
            // Since this is a very niche feature only meant to be used for deep performance work,
            // I think this is fine for now (and I don't think there's anything we can do from
            // userspace anyhow, this is a pure `tracing` issue, unrelated to `re_perf_telemetry`).
        };

        run_impl(
            main_thread_token,
            build_info,
            call_source,
            args,
            tokio_runtime.handle(),
            #[cfg(feature = "native_viewer")]
            profiler,
        )
    };

    match res {
        // Clean success
        Ok(()) => Ok(0),

        // Clean failure -- known error AddrInUse
        Err(err)
            if err
                .downcast_ref::<std::io::Error>()
                .is_some_and(|io_err| io_err.kind() == std::io::ErrorKind::AddrInUse) =>
        {
            re_log::warn!("{err:#}");
            Ok(1)
        }

        // Unclean failure -- re-raise exception
        Err(err) => Err(err),
    }
}

#[cfg(feature = "native_viewer")]
fn should_relaunch_detached(args: &Args) -> bool {
    // Destructure to ensure we consider all fields when adding new ones.
    let Args {
        detach_process,
        detached_process_child,
        command,
        serve_grpc,
        serve_web,
        web_viewer,
        save,
        test_receive,
        version,

        headless: _,
        integration_test: _,
        bind: _,
        memory_limit: _,
        server_memory_limit: _,
        newest_first: _,
        cors_allow_origin: _,
        persist_state: _,
        port: _,
        new: _,
        profile: _,
        screenshot_to: _,
        connect: _,
        expect_data_soon: _,
        threads: _,
        url_or_paths: _,
        web_viewer_port: _,
        hide_welcome_screen: _,
        window_size: _,
        renderer: _,
        video_decoder: _,
    } = args;

    *detach_process
        && !detached_process_child
        && command.is_none()
        && !serve_grpc
        && !serve_web
        && !web_viewer
        && save.is_none()
        && !test_receive
        && !version
}

#[cfg(feature = "native_viewer")]
fn detached_child_args(raw_args: &[std::ffi::OsString]) -> Vec<&std::ffi::OsStr> {
    let mut child_args = raw_args
        .iter()
        .skip(1)
        .map(std::ffi::OsString::as_os_str)
        .collect::<Vec<_>>();
    let insertion_index = child_args
        .iter()
        .position(|arg| *arg == std::ffi::OsStr::new("--"))
        .unwrap_or(child_args.len());
    child_args.insert(
        insertion_index,
        std::ffi::OsStr::new("--detached-process-child"),
    );
    child_args
}

#[cfg(feature = "native_viewer")]
fn relaunch_detached(raw_args: &[std::ffi::OsString]) -> anyhow::Result<()> {
    let executable = std::env::current_exe()
        .map_err(|err| anyhow::anyhow!("failed to locate the Rerun executable: {err}"))?;
    let mut command = std::process::Command::new(executable);
    command
        .args(detached_child_args(raw_args))
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());

    #[cfg(target_family = "unix")]
    {
        use std::os::unix::process::CommandExt as _;

        // SAFETY: This runs in the forked child before exec and only calls the
        // async-signal-safe `setsid`.
        #[expect(unsafe_code)]
        unsafe {
            command.pre_exec(|| {
                if libc::setsid() == -1 {
                    Err(std::io::Error::last_os_error())
                } else {
                    Ok(())
                }
            });
        }
    }

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt as _;

        const DETACHED_PROCESS: u32 = 0x0000_0008;
        command.creation_flags(DETACHED_PROCESS);
    }

    command
        .spawn()
        .map_err(|err| anyhow::anyhow!("failed to launch the detached Rerun Viewer: {err}"))?;
    Ok(())
}

fn run_impl(
    _main_thread_token: crate::MainThreadToken,
    _build_info: re_build_info::BuildInfo,
    _call_source: CallSource,
    args: Args,
    tokio_runtime_handle: &tokio::runtime::Handle,
    #[cfg(feature = "native_viewer")] profiler: re_tracing::Profiler,
) -> anyhow::Result<()> {
    //TODO(#10068): populate token passed with `--token`
    let connection_registry = if args.integration_test {
        re_redap_client::ConnectionRegistry::new_without_stored_credentials()
    } else {
        re_redap_client::ConnectionRegistry::new_with_stored_credentials()
    };
    let async_runtime = re_async::AsyncRuntimeHandle::new_native(tokio_runtime_handle.clone());

    let wants_new = args.new || args.port.is_auto();
    let port = args.port.port();

    let server_addr = if wants_new
        && is_another_server_already_running(std::net::SocketAddr::new(args.bind, port))
    {
        let default_port = port;
        let free_port = find_free_port(args.bind)?;
        re_log::info!(
            "Default port {default_port} is already in use, using port {free_port} instead."
        );
        std::net::SocketAddr::new(args.bind, free_port)
    } else {
        std::net::SocketAddr::new(args.bind, port)
    };

    #[cfg(feature = "server")]
    let server_options = re_sdk::ServerOptions {
        playback_behavior: re_sdk::PlaybackBehavior::from_newest_first(args.newest_first),

        memory_limit: {
            re_log::debug!("Parsing --server-memory-limit (for gRPC server)");
            let limit = args.server_memory_limit.as_str();
            re_log::debug!("Server memory limit: {limit}");
            re_memory::MemoryLimit::parse(limit)
                .map_err(|err| anyhow::format_err!("Bad --server-memory-limit: {err}"))?
        },

        cors_allowed_origins: args.cors_allow_origin.clone(),
    };

    // All URLs that we want to process.
    #[allow(clippy::allow_attributes, unused_mut)]
    let mut url_or_paths = args.url_or_paths.clone();

    // Passing `--connect` accounts to adding a proxy URL to the list of URLs that we want to process.
    #[cfg(feature = "server")]
    if let Some(url) = args.connect.clone() {
        let url = url.unwrap_or_else(|| format!("rerun+http://{server_addr}/proxy"));
        if let Err(err) = url.as_str().parse::<re_uri::RedapUri>() {
            anyhow::bail!("expected `/proxy` endpoint: {err}");
        }
        url_or_paths.push(url);
    }

    // Now what do we do with the data?
    if args.test_receive || args.save.is_some() {
        let receivers = ReceiversFromUrlParams::new(
            url_or_paths,
            &UrlParamProcessingConfig::convert_everything_to_data_sources(),
            &connection_registry,
            &async_runtime,
            None,
        )?;
        save_or_test_receive(
            args.save,
            receivers,
            #[cfg(feature = "server")]
            server_addr,
            #[cfg(feature = "server")]
            server_options,
        )
    } else if args.serve_grpc {
        cfg_select! {
            feature = "server" => {
                let receivers = ReceiversFromUrlParams::new(
                    url_or_paths,
                    &UrlParamProcessingConfig::convert_everything_to_data_sources(),
                    &connection_registry,
                    &async_runtime,
                    None,
                )?;
                serve_grpc(
                    receivers,
                    tokio_runtime_handle,
                    server_addr,
                    server_options,
                )
            }
            _ => Err(anyhow::anyhow!(
                "rerun-cli must be compiled with the 'server' feature enabled"
            )),
        }
    } else if args.serve_web {
        cfg_select! {
            not(feature = "server") => Err(anyhow::anyhow!(
                "Can't host server - rerun was not compiled with the 'server' feature"
            )),
            not(feature = "web_viewer") => Err(anyhow::anyhow!(
                "Can't host web-viewer - rerun was not compiled with the 'web_viewer' feature"
            )),
            _ => {
                // We always host the web-viewer in case the users wants it,
                // but we only open a browser automatically with the `--web-viewer` flag.
                let open_browser = args.web_viewer;

                let receivers = ReceiversFromUrlParams::new(
                    url_or_paths,
                    &UrlParamProcessingConfig::grpc_server_and_web_viewer(),
                    &connection_registry,
                    &async_runtime,
                    None,
                )?;
                #[cfg(all(feature = "server", feature = "web_viewer"))]
                serve_web(
                    receivers,
                    args.web_viewer_port,
                    args.renderer,
                    args.video_decoder,
                    server_addr,
                    server_options,
                    open_browser,
                    async_runtime.clone(),
                    connection_registry.clone(),
                )
            }
        }
    } else if !wants_new && args.connect.is_none() && is_another_server_already_running(server_addr)
    {
        let receivers = ReceiversFromUrlParams::new(
            url_or_paths,
            &UrlParamProcessingConfig::convert_everything_to_data_sources(),
            &connection_registry,
            &async_runtime,
            None,
        )?;
        connect_to_existing_server(receivers, server_addr)
    } else {
        cfg_select! {
            feature = "native_viewer" => start_native_viewer(
                &args,
                url_or_paths,
                _main_thread_token,
                _build_info,
                _call_source,
                async_runtime,
                profiler,
                connection_registry,
                #[cfg(feature = "server")]
                server_addr,
                #[cfg(feature = "server")]
                server_options,
            ),
            _ => Err(anyhow::anyhow!(
                "Can't start viewer - rerun was compiled without the 'native_viewer' feature"
            )),
        }
    }
}

#[cfg(feature = "native_viewer")]
#[allow(clippy::allow_attributes, unused_variables)]
fn start_native_viewer(
    args: &Args,
    url_or_paths: Vec<String>,
    _main_thread_token: re_viewer::MainThreadToken,
    _build_info: re_build_info::BuildInfo,
    call_source: CallSource,
    async_runtime: re_async::AsyncRuntimeHandle,
    profiler: re_tracing::Profiler,
    connection_registry: re_redap_client::ConnectionRegistryHandle,
    #[cfg(feature = "server")] server_addr: std::net::SocketAddr,
    #[cfg(feature = "server")] server_options: re_sdk::ServerOptions,
) -> anyhow::Result<()> {
    use re_viewer::external::{eframe, re_viewer_context};

    use crate::external::re_ui::{UICommand, UICommandSender as _};

    let startup_options = native_startup_options_from_args(args)?;

    let integration_test = args.integration_test;
    let connect = args.connect.is_some();
    let renderer = args.renderer.as_deref();
    let memory_limit = args
        .memory_limit
        .as_ref()
        .map(|memory_limit| {
            re_log::debug!("Parsing --memory-limit (for Viewer)");
            re_memory::MemoryLimit::parse(memory_limit)
        })
        .transpose()
        .map_err(|err| anyhow::format_err!("Bad --memory-limit: {err}"))?;

    let (command_tx, command_rx) = re_viewer_context::command_channel();

    let auth_error_handler = re_viewer::App::auth_error_handler(command_tx.clone());

    // Start catching `re_log::info/warn/error` messages
    // so we can show them in the notification panel.
    // In particular: create this before calling `run_native_app`
    // so we catch any warnings produced during startup.
    let text_log_rx = re_viewer::register_text_log_receiver();

    #[allow(clippy::allow_attributes, unused_mut)]
    let ReceiversFromUrlParams {
        mut log_receivers,
        urls_to_pass_on_to_viewer,
    } = ReceiversFromUrlParams::new(
        url_or_paths,
        &UrlParamProcessingConfig::native_viewer(),
        &connection_registry,
        &async_runtime,
        Some(auth_error_handler),
    )?;

    let create_app = move |cc: &eframe::CreationContext<'_>| -> re_viewer::App {
        {
            let tx = command_tx.clone();
            let egui_ctx = cc.egui_ctx.clone();
            async_runtime.spawn_future(async move {
                // We catch ctrl-c commands so we can properly quit.
                // Without this, recent state changes might not be persisted.
                match tokio::signal::ctrl_c().await {
                    Ok(()) => {
                        re_log::info!("Caught Ctrl-C, quitting Rerun Viewer…");
                        tx.send_ui(UICommand::Quit);
                        egui_ctx.request_repaint();
                    }
                    Err(err) => {
                        re_log::error!("Failed to listen for ctrl-c signal: {err}");
                    }
                }
            });
        }
        let app_env = if integration_test {
            re_viewer::AppEnvironment::Test
        } else {
            call_source.app_env()
        };
        let mut app = re_viewer::App::with_commands(
            _main_thread_token,
            _build_info,
            app_env,
            startup_options,
            cc,
            Some(connection_registry.clone()),
            async_runtime,
            text_log_rx,
            (command_tx, command_rx),
        );

        if let Some(memory_limit) = memory_limit {
            app.app_options_mut().memory_limit = memory_limit;
        }

        // If we're **not** connecting to an existing server, we spawn a new one and add it to the list of receivers.
        #[cfg(feature = "server")]
        if !connect {
            // The internal catalog is served (loopback-only) on the proxy server's port below, and
            // also reached in-process by the viewer.
            #[cfg(not(target_arch = "wasm32"))]
            let internal_catalog = re_viewer::internal_catalog::build(server_addr);
            #[cfg(not(target_arch = "wasm32"))]
            connection_registry.set_internal(internal_catalog.connection.clone());

            #[cfg_attr(target_arch = "wasm32", expect(unused_mut))]
            let mut extra_services = re_grpc_server::LoopbackServices::default();

            #[cfg(not(target_arch = "wasm32"))]
            extra_services.add_service(internal_catalog.grpc_service());

            let (log_receiver, grpc_server_handle) = re_grpc_server::spawn_with_recv_and_services(
                server_addr,
                server_options,
                re_grpc_server::shutdown::never(),
                extra_services,
            );

            log_receivers.push(log_receiver);

            struct ProxyHandleWrapper {
                handle: re_grpc_server::MessageProxyHandle,
            }

            impl re_viewer::ExternalMemoryUser for ProxyHandleWrapper {
                fn capture(&mut self) -> Option<re_byte_size::NamedMemUsageTree> {
                    self.handle
                        .capture_memory()
                        .map(|tree| re_byte_size::NamedMemUsageTree {
                            name: "GRPC Server".to_owned(),
                            value: tree,
                        })
                }
            }

            app.add_external_memory_user(Box::new(ProxyHandleWrapper {
                handle: grpc_server_handle,
            }));
        }

        app.set_profiler(profiler);
        for rx in log_receivers {
            app.add_log_receiver(rx);
        }
        for url in urls_to_pass_on_to_viewer {
            app.open_url_or_file(&url);
        }
        if let Ok(url) = std::env::var("EXAMPLES_MANIFEST_URL") {
            app.set_examples_manifest_url(url);
        }

        app
    };

    if args.headless {
        let window_size = args
            .window_size
            .as_deref()
            .map(parse_size)
            .transpose()?
            .map(|[w, h]| re_viewer::external::egui::Vec2::new(w, h));

        re_viewer::run_headless_app(Box::new(create_app), renderer, window_size)
            .map_err(|err| err.into())
    } else {
        re_viewer::run_native_app(
            _main_thread_token,
            Box::new(move |cc| Ok(Box::new(create_app(cc)))),
            renderer,
        )
        .map_err(|err| err.into())
    }
}

#[cfg(feature = "native_viewer")]
fn native_startup_options_from_args(args: &Args) -> anyhow::Result<re_viewer::StartupOptions> {
    re_tracing::profile_function!();

    let video_decoder_hw_acceleration = args.video_decoder.as_ref().and_then(|s| match s.parse() {
        Err(()) => {
            re_log::warn_once!("Failed to parse --video-decoder value: {s}. Ignoring.");
            None
        }
        Ok(hw_accell) => Some(hw_accell),
    });

    Ok(re_viewer::StartupOptions {
        hide_welcome_screen: args.hide_welcome_screen,
        detach_process: args.detach_process,
        persist_state: args.persist_state && !args.integration_test,
        is_in_notebook: false,
        screenshot_to_path_then_quit: args.screenshot_to.clone(),

        expect_data_soon: if args.expect_data_soon {
            Some(true)
        } else {
            None
        },

        // TODO(emilk): make it easy to set this on eframe instead
        resolution_in_points: if let Some(size) = &args.window_size {
            Some(parse_size(size)?)
        } else {
            None
        },
        force_wgpu_backend: args.renderer.clone(),
        video_decoder_hw_acceleration,

        ..Default::default()
    })
}

fn connect_to_existing_server(
    receivers: ReceiversFromUrlParams,
    server_addr: std::net::SocketAddr,
) -> anyhow::Result<()> {
    use re_sdk::sink::LogSink as _;

    let uri: re_uri::ProxyUri = format!("rerun+http://{server_addr}/proxy").parse()?;
    re_log::info!(%uri, "Another viewer is already running, streaming data to it. Use --port auto to force a new viewer.");
    let sink = re_sdk::sink::GrpcSink::new(uri);
    if !receivers.urls_to_pass_on_to_viewer.is_empty() {
        re_log::warn!(
            "The following URLs can't be passed to already open viewers yet: {:?}",
            receivers.urls_to_pass_on_to_viewer
        );
    }
    for rx in receivers.log_receivers {
        while rx.is_connected() {
            while let Ok(msg) = rx.recv() {
                if let Some(msg) = msg.into_data() {
                    match msg {
                        DataSourceMessage::LogMsg(log_msg) => {
                            sink.send(log_msg);
                        }
                        unsupported => {
                            re_log::error_once!(
                                "Can't pass on {} to the server",
                                unsupported.variant_name()
                            );
                        }
                    }
                }
            }
        }
    }
    sink.flush_blocking(Duration::MAX)?;

    Ok(())
}

#[cfg(all(feature = "server", feature = "web_viewer"))]
fn serve_web(
    receivers: ReceiversFromUrlParams,
    web_viewer_port: u16,
    force_wgpu_backend: Option<String>,
    video_decoder: Option<String>,
    server_addr: std::net::SocketAddr,
    server_options: re_sdk::ServerOptions,
    open_browser: bool,
    async_runtime: re_async::AsyncRuntimeHandle,
    connection_registry: re_redap_client::ConnectionRegistryHandle,
) -> anyhow::Result<()> {
    let ReceiversFromUrlParams {
        log_receivers,
        mut urls_to_pass_on_to_viewer,
    } = receivers;

    // Shared receiver set: CLI files + dynamic opens from the web UI (`/api/open_local`).
    let receive_set = std::sync::Arc::new(LogReceiverSet::new(log_receivers));

    // Keepalive so the gRPC forwarder thread never exits when the initial set is empty /
    // after a file finishes streaming — we need it alive for later `/api/open_local` opens.
    let (_keepalive_tx, keepalive_rx) = re_log_channel::log_channel(re_log_channel::LogSource::Sdk);
    // Leak the sender so the channel never closes for the lifetime of the process.
    std::mem::forget(_keepalive_tx);
    receive_set.add(keepalive_rx);

    // Always host the gRPC proxy so `/api/open_local` can stream into connected viewers.
    if server_addr.port() == web_viewer_port {
        anyhow::bail!(
            "Trying to spawn a Web Viewer server on {}, but this port is \
                already used by the server we're connecting to. Please specify a different port.",
            server_addr.port()
        );
    }

    // Spawn a server which the Web Viewer can connect to.
    let _ = re_grpc_server::spawn_from_rx_set(
        server_addr,
        server_options,
        re_grpc_server::shutdown::never(),
        std::sync::Arc::clone(&receive_set),
    );

    // Add the proxy URL for the web viewer only when we have a concrete public
    // bind address. For 0.0.0.0 / loopback, omit it so index.html fills
    // `rerun+http://<page-hostname>:port/proxy` from location.hostname — required
    // when a remote browser opens http://LAN-IP:9090/ (localhost would point at
    // the client machine, not the host that has the data).
    if !(server_addr.ip().is_unspecified() || server_addr.ip().is_loopback()) {
        let proxy_url = format!("rerun+http://{server_addr}/proxy");
        re_log::debug_assert!(
            proxy_url.parse::<re_uri::RedapUri>().is_ok(),
            "Expected a proper proxy URI, but got {proxy_url:?}"
        );
        urls_to_pass_on_to_viewer.push(proxy_url);
    }

    let mcap_topic_cache = std::sync::Arc::new(parking_lot::Mutex::new(McapTopicCache::default()));
    let open_local_dedup = std::sync::Arc::new(parking_lot::Mutex::new(OpenLocalDedup::default()));

    let open_local: re_web_viewer_server::OpenLocalHandler = {
        let receive_set = std::sync::Arc::clone(&receive_set);
        let async_runtime = async_runtime.clone();
        let connection_registry = connection_registry.clone();
        let topic_cache = std::sync::Arc::clone(&mcap_topic_cache);
        let dedup = std::sync::Arc::clone(&open_local_dedup);
        std::sync::Arc::new(move |path: &str| -> Result<(), String> {
            open_local_path_into_receive_set(
                path,
                std::sync::Arc::clone(&receive_set),
                async_runtime.clone(),
                connection_registry.clone(),
                Some(std::sync::Arc::clone(&topic_cache)),
                Some(std::sync::Arc::clone(&dedup)),
            )
        })
    };

    // This is the server that serves the Wasm+HTML:
    let web_server = WebViewerConfig {
        bind_ip: server_addr.ip().to_string(),
        web_port: re_web_viewer_server::WebViewerServerPort(web_viewer_port),
        connect_to: urls_to_pass_on_to_viewer,
        force_wgpu_backend,
        video_decoder,
        open_browser,
        assets_archive_path: None,
    }
    .host_web_viewer()?;
    web_server.set_open_local_handler(open_local);

    let convert_mgr = convert_record::ConvertManager::new();
    let browser_records = browser_record::BrowserRecords::new(
        std::sync::Arc::clone(&convert_mgr),
        convert_record::record_tool(),
        upload_recording_dir().join("browser-streams"),
    );
    web_server.set_browser_record_handler(std::sync::Arc::new(move |query, reader| {
        browser_records.request(query, reader)
    }));
    {
        let mgr = std::sync::Arc::clone(&convert_mgr);
        web_server.set_convert_record_handler(std::sync::Arc::new(move |body: &str| {
            let (path, map) = if body.starts_with('{') {
                let request: serde_json::Value =
                    serde_json::from_str(body).map_err(|e| e.to_string())?;
                let path = request["path"]
                    .as_str()
                    .ok_or("Missing record path")?
                    .to_owned();
                let map = request["map"]
                    .as_str()
                    .filter(|m| !m.trim().is_empty())
                    .map(str::to_owned);
                (path, map)
            } else {
                (body.to_owned(), None)
            };
            let pb = resolve_record_path(&path)?;
            let map = map
                .map(|m| {
                    let path =
                        rewrite_apollo_workspace_aliases(&std::path::PathBuf::from(m.trim()));
                    let path = path
                        .canonicalize()
                        .map_err(|e| format!("HD map path: {e}"))?;
                    if std::env::var_os("WEB_MONITOR_OPEN_ROOTS").is_some()
                        && !open_local_roots().iter().any(|r| path.starts_with(r))
                    {
                        return Err("HD map is outside WEB_MONITOR_OPEN_ROOTS".to_owned());
                    }
                    Ok(path)
                })
                .transpose()?;
            mgr.start_or_cached(pb, map)
        }));
    }
    {
        let mgr = std::sync::Arc::clone(&convert_mgr);
        web_server.set_convert_status_handler(std::sync::Arc::new(move |job_id: &str| {
            mgr.status_json(job_id)
        }));
    }
    web_server.set_upload_recording_handler(std::sync::Arc::new(|filename: &str, bytes: &[u8]| {
        save_uploaded_recording(filename, bytes)
    }));
    {
        let topic_cache = std::sync::Arc::clone(&mcap_topic_cache);
        web_server.set_mcap_topics_handler(std::sync::Arc::new(
            move |path: Option<&str>| match path {
                None => {
                    let cache = topic_cache.lock().clone();
                    if cache.path.is_empty() {
                        Err("no MCAP topics cached yet — open an .mcap first".into())
                    } else {
                        Ok(mcap_topics_json(
                            &cache.path,
                            &cache.topics,
                            cache.begin_ns,
                            cache.end_ns,
                        ))
                    }
                }
                Some(p) => {
                    let pb = resolve_open_local_path(p)?;
                    let ext = pb
                        .extension()
                        .and_then(|e| e.to_str())
                        .unwrap_or("")
                        .to_ascii_lowercase();
                    if ext != "mcap" {
                        return Err(format!("expected .mcap, got .{ext}"));
                    }
                    #[cfg(feature = "importers")]
                    {
                        let (topics, begin_ns, end_ns) = list_mcap_summary_meta(&pb)?;
                        let path_s = pb.display().to_string();
                        re_log::info!(
                            "web_monitor mcap_topics: {} channels from {}",
                            topics.len(),
                            path_s
                        );
                        *topic_cache.lock() = McapTopicCache {
                            path: path_s.clone(),
                            topics: topics.clone(),
                            begin_ns,
                            end_ns,
                        };
                        let mut response: serde_json::Value = serde_json::from_str(
                            &mcap_topics_json(&path_s, &topics, begin_ns, end_ns),
                        )
                        .map_err(|e| e.to_string())?;
                        response["source"] = playback_source_descriptor(&pb, &topics)?;
                        Ok(response.to_string())
                    }
                    #[cfg(not(feature = "importers"))]
                    {
                        let _ = pb;
                        Err("importers feature required for mcap_topics".into())
                    }
                }
            },
        ));
    }
    {
        let sim_worker = parking_lot::Mutex::new(None::<super::debug_query::DebugWorker>);
        web_server.set_simulation_handler(std::sync::Arc::new(move |body: &str| {
            let request: serde_json::Value = serde_json::from_str(body)
                .map_err(|e| format!("Invalid simulation request: {e}"))?;
            let mut worker = sim_worker.lock();
            if worker.is_none() {
                let script = std::env::var("WEB_MONITOR_SIM_SERVICE").unwrap_or_else(|_| {
                    const CANDIDATES: &[&str] = &[
                        "/apollo_workspace/modules/simulation/simulator/task_service.py",
                        "/apollo_workspace/simulation/simulator/task_service.py",
                    ];
                    CANDIDATES
                        .iter()
                        .find(|p| std::path::Path::new(p).is_file())
                        .copied()
                        .unwrap_or(CANDIDATES[0])
                        .into()
                });
                *worker = Some(super::debug_query::DebugWorker::start_script(&script)?);
            }
            let result = worker
                .as_mut()
                .ok_or("Simulation service unavailable")?
                .query(&request);
            if result.is_err() {
                *worker = None;
            }
            result
        }));
        web_server.set_topic_debug_handler(std::sync::Arc::new(
            move |mcap: &str, topic: &str, at_ns: Option<i64>| topic_debug_json(mcap, topic, at_ns),
        ));
        let worker = parking_lot::Mutex::new(None::<super::debug_query::DebugWorker>);
        web_server.set_debug_query_handler(std::sync::Arc::new(move |body: &str| {
            let mut request: serde_json::Value =
                serde_json::from_str(body).map_err(|e| format!("Invalid debug query JSON: {e}"))?;
            let path = request["mcap"].as_str().ok_or("Missing mcap path")?;
            let path = resolve_open_local_path(path)?;
            if path.extension().and_then(|e| e.to_str()) != Some("mcap") || !path.is_file() {
                return Err("Debug queries require an existing MCAP file".into());
            }
            request["mcap"] = path.to_string_lossy().to_string().into();
            let mut guard = worker.lock();
            if guard.is_none() {
                *guard = Some(super::debug_query::DebugWorker::start()?);
            }
            let result = guard
                .as_mut()
                .ok_or("Debug worker unavailable")?
                .query(&request);
            if result.is_err() {
                *guard = None; // kill failed worker; report error, no silent retry
            }
            result
        }));
    }
    {
        let receive_set = std::sync::Arc::clone(&receive_set);
        let session = std::sync::Arc::new(parking_lot::Mutex::new(std::collections::HashMap::<
            String,
            PlaybackSession,
        >::new()));
        web_server.set_playback_window_handler(std::sync::Arc::new(
            move |mcap: &str, begin_ns: u64, end_ns: u64, reset: bool, topics: &[String]| {
                playback_window_into_receive_set(
                    mcap,
                    begin_ns,
                    end_ns,
                    reset,
                    topics,
                    std::sync::Arc::clone(&receive_set),
                    std::sync::Arc::clone(&session),
                )
            },
        ));
    }

    web_server.block();

    Ok(())
}

/// Last MCAP summary topic list (filled on open / explicit `/api/mcap_topics`).
#[cfg(all(feature = "server", feature = "web_viewer"))]
#[derive(Clone, Default)]
struct McapTopicCache {
    path: String,
    topics: Vec<String>,
    begin_ns: Option<u64>,
    end_ns: Option<u64>,
}

/// Stable recording identity across successive `/api/playback_window` imports.
#[cfg(all(feature = "server", feature = "web_viewer"))]
#[derive(Default)]
struct PlaybackSession {
    identity: Option<(String, String)>,
    recording_id: Option<re_log_types::RecordingId>,
    /// True while a window import thread is still running.
    in_flight: bool,
    coverage: super::playback_cache::PlaybackCoverage,
}

#[cfg(all(feature = "server", feature = "web_viewer"))]
fn playback_source_descriptor(
    path: &std::path::Path,
    topics: &[String],
) -> Result<serde_json::Value, String> {
    let meta = std::fs::metadata(path).map_err(|e| format!("Recording source stat failed: {e}"))?;
    let modified = meta
        .modified()
        .map_err(|e| e.to_string())?
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_nanos();
    Ok(
        serde_json::json!({"version":1,"path":path.display().to_string(),"size":meta.len().to_string(),"modified_ns":modified.to_string(),"topics":topics}),
    )
}

/// Deduplicate consecutive `/api/open_local` calls for the same resolved path.
///
/// Importing the same MCAP twice attaches two full streams to the proxy and can
/// deadlock the browser behind gRPC backpressure. Opening a different path resets
/// the guard, so A → B → A remains possible.
#[cfg(all(feature = "server", feature = "web_viewer"))]
#[derive(Default)]
struct OpenLocalDedup {
    last_path: String,
}

#[cfg(all(feature = "server", feature = "web_viewer"))]
impl OpenLocalDedup {
    /// Returns true if this is the same path as the previous accepted open.
    fn should_skip(&mut self, path: &str) -> bool {
        if self.last_path == path {
            return true;
        }
        self.last_path = path.to_owned();
        false
    }

    fn clear_if(&mut self, path: &str) {
        if self.last_path == path {
            self.last_path.clear();
        }
    }
}

/// Read channel topics + message time bounds from the MCAP summary (no message decode).
#[cfg(all(feature = "server", feature = "web_viewer", feature = "importers"))]
fn list_mcap_summary_meta(
    path: &std::path::Path,
) -> Result<(Vec<String>, Option<u64>, Option<u64>), String> {
    let file =
        std::fs::File::open(path).map_err(|err| format!("open {}: {err}", path.display()))?;
    let summary = re_mcap::read_summary(file)
        .map_err(|err| format!("mcap summary {}: {err}", path.display()))?
        .ok_or_else(|| format!("no MCAP summary in {}", path.display()))?;
    let mut topics: Vec<String> = summary
        .channels
        .values()
        .map(|ch| ch.topic.clone())
        .collect();
    topics.sort();
    topics.dedup();

    let (begin_ns, end_ns) = if let Some(stats) = summary.stats.as_ref() {
        if stats.message_count > 0 {
            (Some(stats.message_start_time), Some(stats.message_end_time))
        } else {
            (None, None)
        }
    } else if summary.chunk_indexes.is_empty() {
        (None, None)
    } else {
        let start = summary
            .chunk_indexes
            .iter()
            .map(|c| c.message_start_time)
            .min();
        let end = summary
            .chunk_indexes
            .iter()
            .map(|c| c.message_end_time)
            .max();
        (start, end)
    };

    Ok((topics, begin_ns, end_ns))
}

#[cfg(all(feature = "server", feature = "web_viewer"))]
fn mcap_topics_json(
    path: &str,
    topics: &[String],
    begin_ns: Option<u64>,
    end_ns: Option<u64>,
) -> String {
    let mut body = String::from("{\"path\":");
    body.push_str(&json_string(path));
    body.push_str(",\"count\":");
    body.push_str(&topics.len().to_string());
    body.push_str(",\"begin_ns\":");
    match begin_ns {
        Some(v) => body.push_str(&v.to_string()),
        None => body.push_str("null"),
    }
    body.push_str(",\"end_ns\":");
    match end_ns {
        Some(v) => body.push_str(&v.to_string()),
        None => body.push_str("null"),
    }
    body.push_str(",\"topics\":[");
    for (i, t) in topics.iter().enumerate() {
        if i > 0 {
            body.push(',');
        }
        body.push_str(&json_string(t));
    }
    body.push_str("]}");
    body
}

/// Dreamview-style on-demand DebugString via host Python (fail loud, no UI freeze).
#[cfg(all(feature = "server", feature = "web_viewer"))]
fn topic_debug_json(mcap: &str, topic: &str, at_ns: Option<i64>) -> Result<String, String> {
    let pb = resolve_open_local_path(mcap)?;
    let ext = pb
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if ext != "mcap" {
        return Err(format!("topic_debug expects .mcap, got .{ext}"));
    }
    if !pb.is_file() {
        return Err(format!("mcap not found: {}", pb.display()));
    }

    let script = if let Ok(p) = std::env::var("WEB_MONITOR_TOPIC_DEBUG") {
        std::path::PathBuf::from(p)
    } else {
        convert_record::apollo_record_tools_dir().join("mcap_topic_debug.py")
    };
    if !script.is_file() {
        return Err(format!(
            "topic_debug script missing: {} (set WEB_MONITOR_TOPIC_DEBUG)",
            script.display()
        ));
    }

    let mut cmd = std::process::Command::new("python3");
    cmd.env("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
        .env(
            "PYTHONPATH",
            std::env::var("PYTHONPATH").unwrap_or_else(|_| "/opt/apollo/neo/python".into()),
        )
        .arg(&script)
        .arg("--mcap")
        .arg(&pb)
        .arg("--topic")
        .arg(topic);
    if let Some(ns) = at_ns {
        cmd.arg("--at-ns").arg(ns.to_string());
    }

    let out = cmd
        .output()
        .map_err(|e| format!("failed to spawn topic_debug: {e}"))?;
    let stdout = String::from_utf8_lossy(&out.stdout).trim().to_owned();
    let stderr = String::from_utf8_lossy(&out.stderr).trim().to_owned();
    if stdout.is_empty() {
        return Err(if stderr.is_empty() {
            format!(
                "topic_debug produced no output (exit {:?})",
                out.status.code()
            )
        } else {
            format!("topic_debug failed: {stderr}")
        });
    }
    // Script always prints JSON; non-zero exit still carries {"status":"error",...}.
    if !out.status.success() {
        // Prefer structured JSON error from the script.
        if stdout.contains("\"status\"") {
            return Ok(stdout);
        }
        return Err(format!(
            "topic_debug exit {:?}: {stdout} {stderr}",
            out.status.code()
        ));
    }
    Ok(stdout)
}

#[cfg(all(feature = "server", feature = "web_viewer"))]
fn json_string(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c.is_control() => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// Native-read a host `.rrd` / `.rbl` / `.mcap` and attach its log stream to the gRPC proxy.
///
/// Validation is synchronous; the actual import runs on a background thread so the HTTP
/// `POST /api/open_local` handler can return immediately (large files must not block the
/// web server). Connect a Viewer to the proxy so the channel can drain.
#[cfg(all(feature = "server", feature = "web_viewer"))]
fn open_local_path_into_receive_set(
    path: &str,
    receive_set: std::sync::Arc<LogReceiverSet>,
    async_runtime: re_async::AsyncRuntimeHandle,
    connection_registry: re_redap_client::ConnectionRegistryHandle,
    topic_cache: Option<std::sync::Arc<parking_lot::Mutex<McapTopicCache>>>,
    open_dedup: Option<std::sync::Arc<parking_lot::Mutex<OpenLocalDedup>>>,
) -> Result<(), String> {
    let path = path.trim().trim_matches('"');
    if path.is_empty() {
        return Err("empty path".into());
    }
    if path.contains('\0') || path.contains("..") {
        return Err("invalid path".into());
    }

    let pb = resolve_open_local_path(path)?;

    let ext = pb
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if !matches!(ext.as_str(), "rrd" | "rbl" | "mcap") {
        return Err(format!(
            "unsupported extension .{ext}; expected .rrd / .rbl / .mcap"
        ));
    }

    let path_owned = pb.display().to_string();

    if let Some(dedup) = open_dedup.as_ref() {
        if dedup.lock().should_skip(&path_owned) {
            re_log::info!("web_monitor open_local: skip consecutive duplicate open ({path_owned})");
            return Ok(());
        }
    }

    // Cache MCAP channel topics from the file summary (header) before streaming.
    if ext == "mcap" {
        #[cfg(feature = "importers")]
        if let Some(cache) = topic_cache.as_ref() {
            let topics = match list_mcap_summary_meta(&pb) {
                Ok((topics, begin_ns, end_ns)) => {
                    *cache.lock() = McapTopicCache {
                        path: path_owned.clone(),
                        topics: topics.clone(),
                        begin_ns,
                        end_ns,
                    };
                    topics
                }
                Err(err) => {
                    if let Some(dedup) = open_dedup.as_ref() {
                        dedup.lock().clear_if(&path_owned);
                    }
                    return Err(format!(
                        "MCAP summary validation failed for {path_owned}: {err}"
                    ));
                }
            };
            re_log::info!(
                "web_monitor open_local: cached {} MCAP topics from summary ({path_owned})",
                topics.len()
            );
        }
        #[cfg(not(feature = "importers"))]
        let _ = topic_cache;
    }
    // Drop any previously attached host file streams before starting a new one.
    // Stacked MCAP imports fill the gRPC proxy and freeze playback ("Sender blocked").
    let dropped = {
        let mut n = 0usize;
        receive_set.retain(|r| match r.source() {
            re_log_channel::LogSource::File { .. } => {
                n += 1;
                false
            }
            _ => true,
        });
        n
    };
    if dropped > 0 {
        re_log::info!(
            "web_monitor open_local: dropped {dropped} prior file stream(s) before {path_owned}"
        );
    }

    let dedup_for_thread = open_dedup.clone();
    let path_for_thread_release = path_owned.clone();
    let path_for_spawn_failure = path_owned.clone();
    let spawn_result = std::thread::Builder::new()
        .name(format!(
            "open_local({})",
            pb.file_name().and_then(|s| s.to_str()).unwrap_or("file")
        ))
        .spawn(move || {
            re_log::info!("web_monitor open_local: native streaming {path_owned}");
            let data_source = re_data_source::LogDataSource::File {
                file_source: re_log_types::FileSource::Uri,
                path: pb,
            };
            let on_auth_err: re_data_source::AuthErrorHandler =
                std::sync::Arc::new(|_uri, _err| {});
            match data_source.stream(&async_runtime, on_auth_err, &connection_registry) {
                Ok(rx) => {
                    receive_set.add(rx);
                    re_log::info!("web_monitor open_local: attached stream for {path_owned}");
                }
                Err(err) => {
                    re_log::error!("web_monitor open_local failed for {path_owned}: {err}");
                    if let Some(dedup) = dedup_for_thread.as_ref() {
                        dedup.lock().clear_if(&path_for_thread_release);
                    }
                }
            }
        });
    if let Err(err) = spawn_result {
        if let Some(dedup) = open_dedup.as_ref() {
            dedup.lock().clear_if(&path_for_spawn_failure);
        }
        return Err(format!("failed to spawn open_local thread: {err}"));
    }

    Ok(())
}

/// Escape a topic name so TopicFilter include patterns match it exactly.
#[cfg(all(feature = "server", feature = "web_viewer", feature = "importers"))]
fn regex_escape_literal(s: &str) -> String {
    let mut out = String::with_capacity(s.len() * 2);
    for c in s.chars() {
        if matches!(
            c,
            '\\' | '.' | '+' | '*' | '?' | '(' | ')' | '[' | ']' | '{' | '}' | '|' | '^' | '$'
        ) {
            out.push('\\');
        }
        out.push(c);
    }
    out
}

/// Native-read a filtered MCAP time window and attach it to the gRPC proxy.
///
/// Uses the same `RecordingId` for successive windows of the same path so chunks merge
/// into one recording. `reset=true` drops prior File streams and starts a new recording.
#[cfg(all(feature = "server", feature = "web_viewer"))]
fn playback_window_into_receive_set(
    path: &str,
    begin_ns: u64,
    end_ns: u64,
    reset: bool,
    topics: &[String],
    receive_set: std::sync::Arc<LogReceiverSet>,
    session: std::sync::Arc<parking_lot::Mutex<std::collections::HashMap<String, PlaybackSession>>>,
) -> Result<String, String> {
    #[cfg(not(feature = "importers"))]
    {
        let _ = (path, begin_ns, end_ns, reset, topics, receive_set, session);
        return Err("importers feature required for playback_window".into());
    }

    #[cfg(feature = "importers")]
    {
        use re_log_types::{ApplicationId, RecordingId};
        use re_mcap::{SelectedDecoders, TopicFilter};
        use re_sdk::external::re_importer::{Importer as _, ImporterSettings, McapImporter};
        use re_span::Span;

        // Empty topic list with reset: still run a filtered import that matches nothing so the
        // viewer gets a fresh empty recording (prior topics disappear from the active store).
        let topics_for_filter: Vec<String> = if topics.is_empty() {
            if !reset {
                return Err(
                    "playback_window: no topics selected — enable topics in Topics picker".into(),
                );
            }
            vec!["__web_monitor_no_topics__".to_owned()]
        } else {
            topics.to_vec()
        };

        if end_ns <= begin_ns {
            return Err(format!(
                "playback_window: begin_ns ({begin_ns}) must be < end_ns ({end_ns})"
            ));
        }

        let path = path.trim().trim_matches('"');
        if path.is_empty() {
            return Err("playback_window: empty path".into());
        }
        if path.contains('\0') || path.contains("..") {
            return Err("playback_window: invalid path".into());
        }

        let pb = resolve_open_local_path(path)?;
        let ext = pb
            .extension()
            .and_then(|e| e.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if ext != "mcap" {
            return Err(format!("playback_window expects .mcap, got .{ext}"));
        }

        let path_owned = pb.display().to_string();
        let source_descriptor = playback_source_descriptor(&pb, topics)?;
        let plan_script = convert_record::apollo_record_tools_dir().join("mcap_playback_plan.py");
        let plan_output = std::process::Command::new("python3")
            .arg(&plan_script)
            .arg(&pb)
            .arg(begin_ns.to_string())
            .arg(end_ns.to_string())
            .arg(if reset { "1" } else { "0" })
            .args(&topics_for_filter)
            .output()
            .map_err(|err| format!("Playback index failed: {err}"))?;
        if !plan_output.status.success() {
            return Err(format!(
                "Playback index failed: {}",
                String::from_utf8_lossy(&plan_output.stderr)
            ));
        }
        let plan: serde_json::Value = serde_json::from_slice(&plan_output.stdout)
            .map_err(|err| format!("Invalid playback plan: {err}"))?;
        let import_begin_ns = plan["import_begin_ns"]
            .as_u64()
            .ok_or("Missing import begin")?;
        let end_ns = plan["end_ns"].as_u64().ok_or("Missing import end")?;
        let seek_ns = plan["seek_ns"].as_u64().ok_or("Missing seek time")?;
        let ready_ns = plan["ready_ns"].as_u64().ok_or("Missing ready time")?;
        let topics_for_filter: Vec<String> = serde_json::from_value(plan["topics"].clone())
            .map_err(|err| format!("Invalid playback topics: {err}"))?;
        let topic_ranges: Vec<(String, Vec<(u64, u64)>)> = topics_for_filter
            .iter()
            .map(|topic| {
                serde_json::from_value(plan["topic_import_ranges"][topic].clone())
                    .map(|ranges| (topic.clone(), ranges))
                    .map_err(|err| format!("Invalid import ranges for topic {topic}: {err}"))
            })
            .collect::<Result<_, _>>()?;
        Span::try_from_start_end(import_begin_ns, end_ns)
            .filter(|s| !s.is_empty())
            .ok_or_else(|| {
                format!("playback_window: invalid half-open range [{begin_ns}, {end_ns})")
            })?;

        let (recording_id, imports) = {
            let mut sessions = session.lock();
            let sess = sessions.entry(path_owned.clone()).or_default();
            let identity = (
                source_descriptor["size"]
                    .as_str()
                    .expect("descriptor size")
                    .to_owned(),
                source_descriptor["modified_ns"]
                    .as_str()
                    .expect("descriptor mtime")
                    .to_owned(),
            );
            let source_changed = sess.identity.as_ref().is_some_and(|old| old != &identity);
            if !reset && source_changed {
                return Err(
                    "Recording source changed during playback; reopen the intended file.".into(),
                );
            }
            if sess.in_flight {
                return Err(
                    "playback_window busy — prior window still importing; retry shortly".into(),
                );
            }
            sess.identity = Some(identity);
            let recording_id = if source_changed || sess.recording_id.is_none() {
                // Other pages may be consuming a different recording. Never
                // discard their queued streams or replace an unchanged source's
                // identity when another page reloads it. Otherwise the first
                // page loses its cached windows and cursor on the next receipt.
                let id = RecordingId::random();
                sess.recording_id = Some(id.clone());
                sess.coverage.clear();
                sess.in_flight = true;
                id
            } else {
                sess.in_flight = true;
                sess.recording_id.clone().expect("checked above")
            };
            let mut imports = std::collections::BTreeMap::<(u64, u64), Vec<String>>::new();
            for (topic, ranges) in &topic_ranges {
                for &(topic_begin, topic_end) in ranges {
                    // Reset is a CLIENT bootstrap: re-send its requested data
                    // even if server coverage predates proxy cache eviction.
                    // Keep the immutable recording identity for existing pages.
                    let missing = if reset {
                        vec![(topic_begin, topic_end)]
                    } else {
                        sess.coverage.missing(topic, topic_begin, topic_end)
                    };
                    for range in missing {
                        imports.entry(range).or_default().push(topic.clone());
                    }
                }
            }
            (recording_id, imports)
        };

        let application_id = pb
            .file_stem()
            .and_then(|s| s.to_str())
            .map(|s| s.replace('.', "_"))
            .and_then(|name| ApplicationId::try_new(name).ok())
            .unwrap_or_else(|| ApplicationId::try_new("apollo_playback").expect("valid"));

        let settings = ImporterSettings {
            application_id: Some(application_id),
            force_store_info: true,
            ..ImporterSettings::recommended(recording_id)
        };

        let (log_tx, log_rx) =
            re_log_channel::log_channel(re_log_channel::LogSource::File { path: pb.clone() });
        receive_set.add(log_rx);

        let topics_owned: Vec<String> = topics.to_vec();
        let topic_count = topics_owned.len();
        let session_for_thread = std::sync::Arc::clone(&session);
        let path_for_response = path_owned.clone();
        let receipt = format!("/__web_monitor_buffer/{}", re_chunk::RowId::new());
        let receipt_for_thread = receipt.clone();
        let spawn_result = std::thread::Builder::new()
            .name(format!("playback_window({begin_ns}..{end_ns})"))
            .spawn(move || -> Result<(), String> {
                re_log::info!(
                    "web_monitor playback_window: [{begin_ns}, {end_ns}) topics={topic_count} path={path_owned}"
                );

                for ((start, end), topics) in imports {
                    let include: Vec<_> = topics.iter()
                        .map(|t| format!("^{}$", regex_escape_literal(t))).collect();
                    let topic_filter = TopicFilter::default().with_include_patterns(&include)
                        .map_err(|err| err.to_string())?;
                    let span = Span::try_from_start_end(start, end).ok_or("Invalid import range")?;
                    let importer = McapImporter::new(&SelectedDecoders::All)
                    .with_topic_filter(topic_filter)
                    .with_time_range(Some(span));

                let (imp_tx, imp_rx) = crossbeam::channel::unbounded();
                if let Err(err) = importer.import_from_path(&settings, pb.clone(), imp_tx) {
                    re_log::error!("playback_window import failed: {err}");
                    let _ = log_tx.quit(Some(Box::new(std::io::Error::other(err.to_string()))));
                    session_for_thread.lock().get_mut(&path_owned).expect("session exists").in_flight = false;
                    return Err(format!("Playback import failed: {err}"));
                }

                for data in imp_rx {
                    match data.into_log_msg() {
                        Ok(msg) => {
                            if log_tx.send(msg.into()).is_err() {
                                return Err("Playback stream disconnected".into());
                            }
                        }
                        Err(err) => {
                            return Err(format!("Playback chunk serialization failed: {err}"));
                        }
                    }
                }
                    let mut sessions = session_for_thread.lock();
                    let session = sessions.get_mut(&path_owned).expect("session exists");
                    for topic in topics { session.coverage.insert(topic, start, end); }
                }
                // Source ownership survives proxy replay/new tabs. This is static
                // metadata, not another timeline and not a global last-file guess.
                let source = re_chunk::Chunk::builder("/__web_monitor_session/source")
                    .with_archetype(re_chunk::RowId::new(), re_log_types::TimePoint::STATIC,
                        &re_sdk_types::archetypes::TextDocument::new(source_descriptor.to_string()))
                    .build().map_err(|e| e.to_string())?;
                log_tx.send(re_log_types::LogMsg::ArrowMsg(settings.recommended_store_id(),
                    source.to_arrow_msg().map_err(|e| e.to_string())?).into()).map_err(|e| e.to_string())?;
                // Ordered after all payloads on the same stream. The browser only
                // marks a window cached when this entity arrives in its EntityDb.
                let marker = re_chunk::Chunk::builder(receipt_for_thread)
                    .with_archetype(
                        re_chunk::RowId::new(),
                        // Static chunks are replayed first to late subscribers.
                        // Keep receipts temporal so they stay after their payloads.
                        re_log_types::TimePoint::from_iter([
                            (re_log_types::Timeline::new_timestamp("publish_time"), begin_ns as i64),
                            (re_log_types::Timeline::new_timestamp("message_time"), begin_ns as i64),
                        ]),
                        &re_sdk_types::archetypes::TextDocument::new("buffered"),
                    )
                    .build().map_err(|err| err.to_string())?;
                let marker = re_log_types::LogMsg::ArrowMsg(
                    settings.recommended_store_id(),
                    marker.to_arrow_msg().map_err(|err| err.to_string())?,
                );
                log_tx.send(marker.into()).map_err(|err| err.to_string())?;
                let _ = log_tx.quit(None);
                session_for_thread.lock().get_mut(&path_owned).expect("session exists").in_flight = false;
                re_log::info!(
                    "web_monitor playback_window: finished [{begin_ns}, {end_ns}) topics={topic_count}"
                );
                Ok(())
            });

        let result = spawn_result
            .map_err(|err| format!("failed to spawn playback_window thread: {err}"))
            .and_then(|thread| {
                thread
                    .join()
                    .map_err(|_| "Playback import panicked".to_owned())
            })
            .and_then(|result| result);
        session
            .lock()
            .get_mut(&path_for_response)
            .expect("session exists")
            .in_flight = false;
        result?;

        Ok(format!(
            r#"{{"status":"ok","path":{},"begin_ns":{begin_ns},"end_ns":{end_ns},"seek_ns":{seek_ns},"ready_ns":{ready_ns},"receipt":{},"topics":{topic_count},"reset":{}}}"#,
            json_string(&path_for_response),
            json_string(&receipt),
            if reset { "true" } else { "false" }
        ))
    }
}

/// Map host checkout paths ↔ in-container `/apollo_workspace` (aem bind-mount).
///
/// Browser/users often paste `/home/.../code/apollo/...` while the viewer process
/// only sees `/apollo_workspace/...`. Without rewrite, convert/open fails with
/// "file not found" and Source shows no recording.
#[cfg(all(feature = "server", feature = "web_viewer"))]
fn rewrite_apollo_workspace_aliases(path: &std::path::Path) -> std::path::PathBuf {
    use std::path::{Path, PathBuf};

    let workspace = std::env::var("APOLLO_ENV_WORKSPACE")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "/home/wangsheng/code/apollo".into());
    let workroot = std::env::var("APOLLO_ENV_WORKROOT")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "/apollo_workspace".into());

    if let Ok(rel) = path.strip_prefix(Path::new(&workspace)) {
        let mapped = Path::new(&workroot).join(rel);
        // Prefer container path when host path is invisible inside aem.
        if mapped.exists() || !path.exists() {
            return mapped;
        }
    }
    if let Ok(rel) = path.strip_prefix(Path::new(&workroot)) {
        let mapped = Path::new(&workspace).join(rel);
        if mapped.exists() && !path.exists() {
            return mapped;
        }
    }
    path.to_path_buf()
}

/// Absolute path, or basename resolved under `WEB_MONITOR_OPEN_ROOTS` / Apollo bag dirs.
#[cfg(all(feature = "server", feature = "web_viewer"))]
fn resolve_open_local_path(path: &str) -> Result<std::path::PathBuf, String> {
    use std::path::PathBuf;

    let candidate = rewrite_apollo_workspace_aliases(&PathBuf::from(path));
    let roots = open_local_roots();

    if candidate.is_absolute() {
        if !candidate.is_file() {
            return Err(format!(
                "file not found: {path} (resolved {})",
                candidate.display()
            ));
        }
        // Optional allow-list when WEB_MONITOR_OPEN_ROOTS is set.
        if std::env::var_os("WEB_MONITOR_OPEN_ROOTS").is_some() {
            let ok = roots.iter().any(|root| candidate.starts_with(root));
            if !ok {
                return Err(format!(
                    "path not under WEB_MONITOR_OPEN_ROOTS ({}): {path}",
                    roots
                        .iter()
                        .map(|p| p.display().to_string())
                        .collect::<Vec<_>>()
                        .join(":")
                ));
            }
        }
        return Ok(candidate);
    }

    // Browse only gives a file name — search host bag roots (large files OK).
    let name = candidate
        .file_name()
        .ok_or_else(|| format!("invalid path: {path}"))?;
    for root in &roots {
        let direct = root.join(name);
        if direct.is_file() {
            re_log::info!(
                "web_monitor open_local: resolved {path:?} -> {}",
                direct.display()
            );
            return Ok(direct);
        }
        if let Some(found) = find_named_file(root, name, 3) {
            re_log::info!(
                "web_monitor open_local: resolved {path:?} -> {}",
                found.display()
            );
            return Ok(found);
        }
    }

    Err(format!(
        "file not found under open roots ({}): {path}",
        roots
            .iter()
            .map(|p| p.display().to_string())
            .collect::<Vec<_>>()
            .join(":")
    ))
}

#[cfg(all(feature = "server", feature = "web_viewer"))]
fn resolve_record_path(path: &str) -> Result<std::path::PathBuf, String> {
    resolve_open_local_path_any(path, true)
}

#[cfg(all(feature = "server", feature = "web_viewer"))]
fn resolve_open_local_path_any(
    path: &str,
    allow_record: bool,
) -> Result<std::path::PathBuf, String> {
    use std::path::PathBuf;

    let path = path.trim().trim_matches('"');
    if path.is_empty() {
        return Err("empty path".into());
    }
    if path.contains('\0') || path.contains("..") {
        return Err("invalid path".into());
    }

    let candidate = rewrite_apollo_workspace_aliases(&PathBuf::from(path));
    let roots = open_local_roots();

    let resolved = if candidate.is_absolute() {
        if !candidate.is_file() {
            return Err(format!(
                "file not found: {path} (resolved {}) — use /apollo_workspace/... inside aem, or host path under APOLLO_ENV_WORKSPACE",
                candidate.display()
            ));
        }
        if std::env::var_os("WEB_MONITOR_OPEN_ROOTS").is_some() {
            let ok = roots.iter().any(|root| candidate.starts_with(root));
            if !ok {
                return Err(format!("path not under WEB_MONITOR_OPEN_ROOTS: {path}"));
            }
        }
        candidate
    } else {
        let name = candidate
            .file_name()
            .ok_or_else(|| format!("invalid path: {path}"))?;
        let mut found = None;
        for root in &roots {
            let direct = root.join(name);
            if direct.is_file() {
                found = Some(direct);
                break;
            }
            if let Some(p) = find_named_file(root, name, 3) {
                found = Some(p);
                break;
            }
        }
        found.ok_or_else(|| {
            format!(
                "file not found under open roots ({}): {path}",
                roots
                    .iter()
                    .map(|p| p.display().to_string())
                    .collect::<Vec<_>>()
                    .join(":")
            )
        })?
    };

    let ext = resolved
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let name = resolved
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let is_record = name.contains(".record") && !name.ends_with(".rrd") && !name.ends_with(".rbl");
    if allow_record {
        if !(is_record || matches!(ext.as_str(), "rrd" | "rbl" | "mcap" | "record")) {
            return Err(format!(
                "unsupported extension .{ext}; expected .record / .rrd / .rbl / .mcap"
            ));
        }
    } else if !matches!(ext.as_str(), "rrd" | "rbl" | "mcap") {
        return Err(format!(
            "unsupported extension .{ext}; expected .rrd / .rbl / .mcap"
        ));
    }
    Ok(resolved)
}

#[cfg(all(feature = "server", feature = "web_viewer"))]
fn open_local_roots() -> Vec<std::path::PathBuf> {
    use std::path::PathBuf;

    if let Ok(roots) = std::env::var("WEB_MONITOR_OPEN_ROOTS") {
        let parsed: Vec<_> = roots
            .split(':')
            .filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .collect();
        if !parsed.is_empty() {
            return parsed;
        }
    }
    vec![
        PathBuf::from("/apollo_workspace/data/bag"),
        PathBuf::from("/apollo_workspace/data/bag/.wm_uploads"),
        PathBuf::from("/apollo_workspace/data"),
        PathBuf::from("/data/bag"),
        PathBuf::from("/home/wangsheng/code/apollo/data/bag"),
    ]
}

#[cfg(all(feature = "server", feature = "web_viewer"))]
fn upload_recording_dir() -> std::path::PathBuf {
    if let Ok(dir) = std::env::var("WEB_MONITOR_UPLOAD_DIR") {
        if !dir.is_empty() {
            return std::path::PathBuf::from(dir);
        }
    }
    std::path::PathBuf::from("/apollo_workspace/data/bag/.wm_uploads")
}

#[cfg(all(feature = "server", feature = "web_viewer"))]
fn save_uploaded_recording(filename: &str, bytes: &[u8]) -> Result<String, String> {
    use std::io::Write as _;

    if bytes.is_empty() {
        return Err("empty upload".into());
    }
    // Soft cap: browser→host uploads of multi-GB bags are supported but unusual.
    const MAX_BYTES: usize = 8 * 1024 * 1024 * 1024;
    if bytes.len() > MAX_BYTES {
        return Err(format!(
            "upload too large ({} MiB); max is {} MiB",
            bytes.len() / (1024 * 1024),
            MAX_BYTES / (1024 * 1024)
        ));
    }
    let base = std::path::Path::new(filename)
        .file_name()
        .and_then(|s| s.to_str())
        .ok_or("invalid filename")?;
    let safe: String = base
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | '_' | '+') {
                c
            } else {
                '_'
            }
        })
        .collect();
    if safe.is_empty() || safe == "." || safe == ".." {
        return Err("invalid filename".into());
    }
    let lower = safe.to_ascii_lowercase();
    let is_record =
        lower.contains(".record") && !lower.ends_with(".rrd") && !lower.ends_with(".rbl");
    if !(is_record
        || lower.ends_with(".rrd")
        || lower.ends_with(".rbl")
        || lower.ends_with(".mcap"))
    {
        return Err("unsupported type (use .record / .rrd / .rbl / .mcap)".into());
    }

    let dir = upload_recording_dir();
    std::fs::create_dir_all(&dir).map_err(|e| format!("create upload dir: {e}"))?;
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let dest = dir.join(format!("{stamp}_{safe}"));
    {
        let mut file = std::fs::File::create(&dest).map_err(|e| format!("create upload: {e}"))?;
        file.write_all(bytes)
            .map_err(|e| format!("write upload: {e}"))?;
        file.sync_all().map_err(|e| format!("sync upload: {e}"))?;
    }
    let path = dest.canonicalize().unwrap_or(dest).display().to_string();
    re_log::info!(
        "web_monitor upload_recording: saved {} ({} MiB)",
        path,
        bytes.len() / (1024 * 1024)
    );
    Ok(serde_json::json!({"status":"ok","path":path,"bytes":bytes.len()}).to_string())
}

#[cfg(all(feature = "server", feature = "web_viewer"))]
fn find_named_file(
    root: &std::path::Path,
    name: &std::ffi::OsStr,
    max_depth: u32,
) -> Option<std::path::PathBuf> {
    fn walk(
        dir: &std::path::Path,
        name: &std::ffi::OsStr,
        depth: u32,
        max_depth: u32,
    ) -> Option<std::path::PathBuf> {
        if depth > max_depth {
            return None;
        }
        let entries = std::fs::read_dir(dir).ok()?;
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_file() && path.file_name() == Some(name) {
                return Some(path);
            }
            if path.is_dir() {
                if let Some(found) = walk(&path, name, depth + 1, max_depth) {
                    return Some(found);
                }
            }
        }
        None
    }
    walk(root, name, 0, max_depth)
}

#[cfg(feature = "server")]
fn serve_grpc(
    receivers: ReceiversFromUrlParams,
    tokio_runtime_handle: &tokio::runtime::Handle,
    server_addr: std::net::SocketAddr,
    server_options: re_sdk::ServerOptions,
) -> anyhow::Result<()> {
    if !cfg!(feature = "server") {
        anyhow::bail!("Can't host server - rerun was not compiled with the 'server' feature");
    }

    receivers.error_on_unhandled_urls("--serve-grpc")?;

    let (signal, shutdown) = re_grpc_server::shutdown::shutdown();
    // Spawn a server which the Web Viewer can connect to.
    // No dev panel in this mode, so we drop the handle.
    let _ = re_grpc_server::spawn_from_rx_set(
        server_addr,
        server_options,
        shutdown,
        std::sync::Arc::new(LogReceiverSet::new(receivers.log_receivers)),
    );

    // Gracefully shut down the server on SIGINT
    tokio_runtime_handle.block_on(tokio::signal::ctrl_c()).ok();

    signal.stop();

    Ok(())
}

fn save_or_test_receive(
    save: Option<String>,
    receivers: ReceiversFromUrlParams,
    #[cfg(feature = "server")] server_addr: std::net::SocketAddr,
    #[cfg(feature = "server")] server_options: re_sdk::ServerOptions,
) -> anyhow::Result<()> {
    receivers.error_on_unhandled_urls(if save.is_none() {
        "--test-receive"
    } else {
        "--save"
    })?;

    #[allow(clippy::allow_attributes, unused_mut)]
    let mut log_receivers = receivers.log_receivers;

    #[cfg(feature = "server")]
    {
        let (log_rx, _handle) = re_grpc_server::spawn_with_recv(
            server_addr,
            server_options,
            re_grpc_server::shutdown::never(),
        );

        log_receivers.push(log_rx);
    }

    let receive_set = LogReceiverSet::new(log_receivers);

    if let Some(rrd_path) = save {
        Ok(stream_to_rrd_on_disk(&receive_set, &rrd_path.into())?)
    } else {
        assert_receive_into_entity_db(&receive_set).map(|_db| ())
    }
}

fn find_free_port(bind: std::net::IpAddr) -> anyhow::Result<u16> {
    let listener = std::net::TcpListener::bind(std::net::SocketAddr::new(bind, 0))?;
    Ok(listener.local_addr()?.port())
}

fn is_another_server_already_running(server_addr: std::net::SocketAddr) -> bool {
    // Check if there is already a viewer running and if so, send the data to it.
    use std::net::TcpStream;
    if TcpStream::connect_timeout(&server_addr, std::time::Duration::from_secs(1)).is_ok() {
        re_log::info!(
            %server_addr,
            "A process is already listening at this address. Assuming it's a Rerun Viewer."
        );
        true
    } else {
        false
    }
}

// NOTE: This is only used as part of end-to-end tests.
fn assert_receive_into_entity_db(rx: &LogReceiverSet) -> anyhow::Result<re_entity_db::EntityDb> {
    re_log::info!("Receiving messages into a EntityDb…");

    let mut rec: Option<re_entity_db::EntityDb> = None;
    let mut bp: Option<re_entity_db::EntityDb> = None;

    let mut num_messages = 0;

    let timeout = std::time::Duration::from_secs(12);

    loop {
        if !rx.is_connected() {
            anyhow::bail!("Channel disconnected without a Goodbye message.");
        }

        if let Some((_, msg)) = rx.recv_timeout(timeout) {
            re_log::info_once!("Received first message.");

            match msg.payload {
                SmartMessagePayload::Msg(msg) => {
                    match msg {
                        DataSourceMessage::RrdManifest(store_id, manifest) => {
                            let mut_db = match store_id.kind() {
                                re_log_types::StoreKind::Recording => {
                                    rec.get_or_insert_with(|| {
                                        re_entity_db::EntityDb::new(store_id.clone())
                                    })
                                }
                                re_log_types::StoreKind::Blueprint => bp.get_or_insert_with(|| {
                                    re_entity_db::EntityDb::new(store_id.clone())
                                }),
                            };

                            mut_db.add_rrd_manifest_message(manifest);
                        }

                        DataSourceMessage::RrdManifestComplete(store_id) => {
                            let mut_db = match store_id.kind() {
                                re_log_types::StoreKind::Recording => {
                                    rec.get_or_insert_with(|| {
                                        re_entity_db::EntityDb::new(store_id.clone())
                                    })
                                }
                                re_log_types::StoreKind::Blueprint => bp.get_or_insert_with(|| {
                                    re_entity_db::EntityDb::new(store_id.clone())
                                }),
                            };

                            mut_db.mark_rrd_manifest_complete();
                        }

                        DataSourceMessage::LogMsg(msg) => {
                            let mut_db = match msg.store_id().kind() {
                                re_log_types::StoreKind::Recording => {
                                    rec.get_or_insert_with(|| {
                                        re_entity_db::EntityDb::new(msg.store_id().clone())
                                    })
                                }
                                re_log_types::StoreKind::Blueprint => bp.get_or_insert_with(|| {
                                    re_entity_db::EntityDb::new(msg.store_id().clone())
                                }),
                            };

                            mut_db.add_log_msg(&msg)?;
                        }

                        DataSourceMessage::DefaultBlueprintRegistration(_) => {
                            anyhow::bail!(
                                "Received a blueprint registration which can't be stored in an EntityDb"
                            );
                        }

                        DataSourceMessage::TableMsg(_) => {
                            anyhow::bail!(
                                "Received a TableMsg which can't be stored in an EntityDb"
                            );
                        }

                        DataSourceMessage::UiCommand(ui_command) => {
                            anyhow::bail!(
                                "Received a UI command which can't be stored in an EntityDb: {ui_command:?}"
                            );
                        }
                    }

                    num_messages += 1;
                }

                re_log_channel::SmartMessagePayload::Flush { on_flush_done } => {
                    on_flush_done();
                }

                SmartMessagePayload::Quit(err) => {
                    if let Some(err) = err {
                        anyhow::bail!("data source has disconnected unexpectedly: {err}")
                    } else if let Some(db) = rec {
                        anyhow::ensure!(0 < num_messages, "No messages received");
                        re_log::info!("Successfully ingested {num_messages} messages.");
                        return Ok(db);
                    }
                    anyhow::bail!("EntityDb never initialized");
                }
            }
        } else {
            if let Some(db) = rec {
                // TODO(RR-3373): find a proper way to detect client disconnect without timing out.
                re_log::info!(
                    "Timed out after successfully receiving {num_messages} messages. Assuming the client disconnected cleanly.",
                );
                return Ok(db);
            }
            anyhow::bail!(
                "Didn't receive any messages within {} seconds. Giving up.",
                timeout.as_secs()
            );
        }
    }
}

// --- util ---

fn initialize_thread_pool(threads_args: i32) {
    // Name the rayon threads for the benefit of debuggers and profilers:
    let mut builder = rayon::ThreadPoolBuilder::new().thread_name(|i| format!("rayon-{i}"));

    if threads_args < 0 {
        match std::thread::available_parallelism() {
            Ok(cores) => {
                let threads = cores.get().saturating_sub((-threads_args) as _).max(1);
                re_log::debug!("Detected {cores} cores. Using {threads} compute threads.");
                builder = builder.num_threads(threads);
            }
            Err(err) => {
                re_log::warn!("Failed to query system of the number of cores: {err}.");
                // Let rayon decide for itself how many threads to use.
                // Its default is to use as many threads as we have cores,
                // (if rayon manages to figure out how many cores we have).
            }
        }
    } else if threads_args == 1 {
        // 1 means "single-threaded".
        // NOTE: we intentionally do NOT use `.use_current_thread()` here,
        // because that causes deadlocks when code does `rayon::spawn()`
        // followed by blocking on the result (e.g. in `load_file.rs`).
        builder = builder.num_threads(1);
        re_log::info!("Running in single-threaded mode.");
    } else {
        // 0 means "use all cores", and rayon understands that
        builder = builder.num_threads(threads_args as usize);
    }

    if let Err(err) = builder.build_global() {
        re_log::warn!("Failed to initialize rayon thread pool: {err}");
    }
}

fn initialize_tokio_runtime(threads_args: i32) -> std::io::Result<Runtime> {
    use std::sync::atomic::{AtomicUsize, Ordering};

    // Name the tokio threads for the benefit of debuggers and profilers:
    let mut builder = tokio::runtime::Builder::new_multi_thread(); // NOLINT: the CLI process owns this configurable runtime
    builder.thread_name_fn(|| {
        static ATOMIC_ID: AtomicUsize = AtomicUsize::new(0);
        let nr = ATOMIC_ID.fetch_add(1, Ordering::Relaxed);
        format!("tokio-#{nr}")
    });
    builder.enable_all();

    match threads_args.cmp(&0) {
        std::cmp::Ordering::Less => {
            if let Ok(cores) = std::thread::available_parallelism() {
                let threads = cores.get().saturating_sub((-threads_args) as _).max(1);
                builder.worker_threads(threads);
            }
        }
        std::cmp::Ordering::Equal => {
            // 0 means "use default" (typically num CPUs)
        }
        std::cmp::Ordering::Greater => {
            builder.worker_threads(threads_args as usize);
        }
    }

    builder.build()
}

#[cfg(feature = "native_viewer")]
fn run_profiler(args: &Args) -> re_tracing::Profiler {
    let mut profiler = re_tracing::Profiler::default();
    if args.profile {
        profiler.start();
    }
    profiler
}

#[cfg(feature = "native_viewer")]
fn parse_size(size: &str) -> anyhow::Result<[f32; 2]> {
    fn parse_size_inner(size: &str) -> Option<[f32; 2]> {
        let (w, h) = size.split_once('x')?;
        let w = w.parse().ok()?;
        let h = h.parse().ok()?;
        Some([w, h])
    }

    parse_size_inner(size)
        .ok_or_else(|| anyhow::anyhow!("Invalid size {size:?}, expected e.g. 800x600"))
}

// --- io ---

// TODO(cmc): dedicated module for io utils, especially stdio streaming in and out.

fn stream_to_rrd_on_disk(
    rx: &re_log_channel::LogReceiverSet,
    path: &std::path::PathBuf,
) -> Result<(), re_log_encoding::FileSinkError> {
    use re_log_encoding::FileSinkError;

    if path.exists() {
        re_log::warn!(?path, "Overwriting existing file");
    }

    re_log::info!("Saving incoming log stream to {path:?}. Abort with Ctrl-C.");

    let encoding_options = re_log_encoding::rrd::EncodingOptions::PROTOBUF_COMPRESSED;
    let file = std::fs::File::create(path).map_err(|err| FileSinkError::CreateFile {
        path: path.clone(),
        source: err,
    })?;
    let mut encoder = re_log_encoding::Encoder::new_eager(
        re_build_info::CrateVersion::LOCAL,
        encoding_options,
        file,
    )?;

    loop {
        if let Ok(msg) = rx.recv() {
            if let Some(payload) = msg.into_data() {
                match payload {
                    DataSourceMessage::LogMsg(log_msg) => {
                        encoder.append(&log_msg)?;
                    }
                    unsupported => {
                        re_log::error_once!(
                            "Received a {} which can't be stored in a file",
                            unsupported.variant_name()
                        );
                    }
                }
            }
        } else {
            re_log::info!("Log stream disconnected, stopping.");
            break;
        }
    }

    re_log::info!("File saved to {path:?}");

    Ok(())
}

/// Describes how to handle URLs passed on the CLI.
struct UrlParamProcessingConfig {
    data_sources_from_http_urls: bool,
    data_sources_from_redap_datasets: bool,
    data_source_from_filepaths: bool,
}

impl UrlParamProcessingConfig {
    /// Instruct to create data sources for everything we can.
    ///
    /// This is used for pure servers and file redirects.
    fn convert_everything_to_data_sources() -> Self {
        // Write to file makes everything it can a data source.
        Self {
            data_sources_from_http_urls: true,
            data_sources_from_redap_datasets: true,
            data_source_from_filepaths: true,
        }
    }

    #[allow(clippy::allow_attributes, dead_code)] // May be unused depending on feature flags.
    fn grpc_server_and_web_viewer() -> Self {
        // GRPC with web viewer can handle everything except files directly.
        Self {
            data_sources_from_http_urls: false,
            data_sources_from_redap_datasets: false,
            data_source_from_filepaths: true,
        }
    }

    #[allow(clippy::allow_attributes, dead_code)] // May be unused depending on feature flags.
    fn native_viewer() -> Self {
        // Native viewer passes everything on to the viewer unchanged.
        Self {
            data_sources_from_http_urls: false,
            data_sources_from_redap_datasets: false,
            data_source_from_filepaths: false,
        }
    }
}

/// Log receivers created from URLs or path parameters that were passed in on the CLI.
struct ReceiversFromUrlParams {
    /// Log receivers that we want to hook up to a connection or viewer.
    log_receivers: Vec<LogReceiver>,

    /// URLs that should be passed on to the viewer if possible.
    ///
    /// If we can't do that, we should error or warn, see [`Self::error_on_unhandled_urls`].
    urls_to_pass_on_to_viewer: Vec<String>,
}

impl ReceiversFromUrlParams {
    /// Processes all incoming URLs according to the given config.
    fn new(
        input_urls: Vec<String>,
        config: &UrlParamProcessingConfig,
        connection_registry: &re_redap_client::ConnectionRegistryHandle,
        async_runtime: &re_async::AsyncRuntimeHandle,
        auth_error_handler: Option<AuthErrorHandler>,
    ) -> anyhow::Result<Self> {
        let mut data_sources = Vec::new();
        let mut urls_to_pass_on_to_viewer = Vec::new();

        for url in input_urls {
            if let Some(data_source) = LogDataSource::from_uri(
                re_log_types::FileSource::Cli,
                &url,
                &re_data_source::FromUriOptions {
                    accept_extensionless_http: true,
                },
            ) {
                match &data_source {
                    LogDataSource::HttpUrl { .. } => {
                        if config.data_sources_from_http_urls {
                            data_sources.push(data_source);
                        } else {
                            urls_to_pass_on_to_viewer.push(url);
                        }
                    }

                    LogDataSource::RedapProxy(..) | LogDataSource::RedapDatasetSegment { .. } => {
                        if config.data_sources_from_redap_datasets {
                            data_sources.push(data_source);
                        } else {
                            urls_to_pass_on_to_viewer.push(url);
                        }
                    }

                    LogDataSource::File { .. } => {
                        if config.data_source_from_filepaths {
                            data_sources.push(data_source);
                        } else {
                            urls_to_pass_on_to_viewer.push(url);
                        }
                    }

                    LogDataSource::Stdin => {
                        data_sources.push(data_source);
                    }
                }
            } else {
                // We don't have the full url parsing logic here. Just pass it on to the viewer!
                urls_to_pass_on_to_viewer.push(url);
            }
        }

        let auth_error_handler = auth_error_handler.unwrap_or_else(|| {
            std::sync::Arc::new(|uri, err| {
                re_log::error!(?uri, "Authentication error for data source: {err}");
            })
        });

        let log_receivers = data_sources
            .into_iter()
            .map(|data_source| {
                data_source.stream(
                    async_runtime,
                    auth_error_handler.clone(),
                    connection_registry,
                )
            })
            .collect::<anyhow::Result<Vec<_>>>()?;

        Ok(Self {
            log_receivers,
            urls_to_pass_on_to_viewer,
        })
    }

    /// Returns an error if there are any URLs that weren't converted into log receivers.
    fn error_on_unhandled_urls(&self, command: &str) -> anyhow::Result<()> {
        if !self.urls_to_pass_on_to_viewer.is_empty() {
            anyhow::bail!(
                "`{command}` does not support these URLs: {:?}",
                self.urls_to_pass_on_to_viewer
            );
        }
        Ok(())
    }
}

/// Records analytics for the CLI command invocation.
#[cfg(feature = "analytics")]
fn record_cli_command_analytics(args: &Args) {
    let Some(analytics) = re_analytics::Analytics::global_or_init() else {
        return;
    };

    // Destructure to ensure we consider all fields when adding new ones.
    let Args {
        command,
        newest_first,
        persist_state,
        profile,
        save,
        screenshot_to,
        serve_web,
        serve_grpc,
        connect,
        expect_data_soon,
        test_receive,
        hide_welcome_screen,
        detach_process,

        // Not logged
        detached_process_child: _,
        threads: _,
        url_or_paths: _,
        version: _,
        web_viewer,
        web_viewer_port: _,
        window_size: _,
        renderer: _,
        video_decoder: _,
        bind: _,
        memory_limit: _,
        server_memory_limit: _,
        cors_allow_origin: _,
        port: _,
        new: _,
        headless: _,
        integration_test: _,
    } = args;

    let (command, subcommand) = match command {
        #[cfg(feature = "analytics")]
        Some(Command::Analytics(cmd)) => {
            let subcommand = match cmd {
                AnalyticsCommands::Details => "details",
                AnalyticsCommands::Clear => "clear",
                AnalyticsCommands::Email { .. } => "email",
                AnalyticsCommands::Enable => "enable",
                AnalyticsCommands::Disable => "disable",
                AnalyticsCommands::Config => "config",
            };
            ("analytics", Some(subcommand))
        }

        #[cfg(feature = "auth")]
        Some(Command::Auth(cmd)) => {
            let subcommand = match cmd {
                AuthCommands::Login(_) => "login",
                AuthCommands::Logout(_) => "logout",
                AuthCommands::Token(_) => "token",
                AuthCommands::GenerateToken(_) => "generate-token",
            };
            ("auth", Some(subcommand))
        }

        Some(Command::Manual) => ("man", None),

        #[cfg(feature = "importers")]
        Some(Command::Mcap(_cmd)) => {
            // TODO(RR-4073): Re-enable analytics for MCAP commands.
            return;
        }

        #[cfg(feature = "native_viewer")]
        Some(Command::ViewerMcp) => ("viewer-mcp", None),

        Some(Command::Download(_)) => ("download", None),

        #[cfg(feature = "native_viewer")]
        Some(Command::Reset) => ("reset", None),

        Some(Command::Rrd(_cmd)) => {
            // TODO(RR-4073): Re-enable analytics for RRD commands.
            return;
        }

        #[cfg(feature = "oss_server")]
        Some(Command::Server(_)) => ("server", None),

        None => ("viewer", None),
    };

    analytics.record(re_analytics::event::CliCommandInvoked {
        command,
        subcommand,
        web_viewer: *web_viewer,
        serve_web: *serve_web,
        serve_grpc: *serve_grpc,
        connect: connect.is_some(),
        save: save.is_some(),
        screenshot_to: screenshot_to.is_some(),
        newest_first: *newest_first,
        persist_state_disabled: !persist_state,
        profile: *profile,
        expect_data_soon: *expect_data_soon,
        hide_welcome_screen: *hide_welcome_screen,
        detach_process: *detach_process,
        test_receive: *test_receive,
    });
}

#[cfg(all(test, feature = "native_viewer"))]
mod tests {
    use super::*;

    use clap::Parser as _;

    #[test]
    fn detach_relaunches_a_native_viewer_exactly_once() {
        for cli_args in [
            &["rerun", "--detach-process"][..],
            &["rerun", "--detach-process", "recording.rrd"],
            &["rerun", "--detach-process", "--headless"],
            &[
                "rerun",
                "--detach-process",
                "--screenshot-to",
                "screenshot.png",
            ],
        ] {
            let raw_args = cli_args
                .iter()
                .copied()
                .map(std::ffi::OsString::from)
                .collect::<Vec<_>>();
            let parent = Args::try_parse_from(raw_args.iter()).unwrap();
            assert!(
                should_relaunch_detached(&parent),
                "expected relaunch for {cli_args:?}"
            );

            let child_args = detached_child_args(&raw_args);
            let child = Args::try_parse_from(std::iter::chain(
                std::iter::once(std::ffi::OsStr::new("rerun")),
                child_args,
            ))
            .unwrap();
            assert!(child.detached_process_child);
            assert!(
                !should_relaunch_detached(&child),
                "unexpected second relaunch for {cli_args:?}"
            );
        }
    }

    #[test]
    fn detach_child_marker_precedes_end_of_options_separator() {
        let raw_args =
            ["rerun", "--detach-process", "--", "recording.rrd"].map(std::ffi::OsString::from);
        let parent = Args::try_parse_from(raw_args.iter()).unwrap();
        assert!(should_relaunch_detached(&parent));

        let child_args = detached_child_args(&raw_args);

        assert_eq!(
            child_args,
            [
                std::ffi::OsStr::new("--detach-process"),
                std::ffi::OsStr::new("--detached-process-child"),
                std::ffi::OsStr::new("--"),
                std::ffi::OsStr::new("recording.rrd"),
            ]
        );

        let child = Args::try_parse_from(std::iter::chain(
            std::iter::once(std::ffi::OsStr::new("rerun")),
            child_args,
        ))
        .unwrap();
        assert!(child.detached_process_child);
        assert!(!should_relaunch_detached(&child));
        assert_eq!(child.url_or_paths, ["recording.rrd"]);
    }

    #[test]
    fn detach_does_not_relaunch_non_viewer_modes() {
        for cli_args in [
            &["rerun", "--detach-process", "--serve-grpc"][..],
            &["rerun", "--detach-process", "--serve-web"],
            &["rerun", "--detach-process", "--web-viewer"],
            &["rerun", "--detach-process", "--save", "output.rrd"],
            &["rerun", "--detach-process", "--test-receive"],
            &["rerun", "--detach-process", "--version"],
            &["rerun", "--detach-process", "reset"],
        ] {
            let args = Args::try_parse_from(cli_args).unwrap();
            assert!(
                !should_relaunch_detached(&args),
                "unexpected relaunch for {cli_args:?}"
            );
        }
    }
}
