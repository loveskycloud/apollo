load("//tools:cpplint.bzl", "cpplint")
load("//tools:apollo_package.bzl", "apollo_cc_binary", "apollo_package")
load("//tools/install:install.bzl", "install_src_files")

package(default_visibility = ["//visibility:public"])

# The native launcher uses workspace sources; Rust is built by build_viewer.sh.
# Never dereference runtime task links / copy target or test-artifact trees.
install_src_files(name = "install_src", type = "neo")

apollo_cc_binary(
    name = "web_monitor_main",
    srcs = ["web_monitor_main.cc"],
    data = [
        ":layout_files",
        ":rerun_cli",
    ],
    deps = [
        "@com_github_gflags_gflags//:gflags",
    ],
)

filegroup(
    name = "layout_files",
    srcs = glob(["layouts/**"], allow_empty = True),
)

filegroup(
    name = "runtime_data",
    srcs = glob(
        [
            "conf/**",
            "layouts/**",
            "scripts/**",
            "blueprints/**",
            "bin/**",
        ],
        allow_empty = True,
    ),
)

# Builds the forked Rerun viewer via cargo. Requires rustup/cargo in the Apollo container.
# Prefer: bash simulation/web_monitor/scripts/build_viewer.sh
# Then: buildtool build -p simulation/web_monitor (picks up bin/rerun via filegroup).
genrule(
    name = "rerun_cli",
    srcs = [
        "scripts/build_viewer.sh",
        "rerun/Cargo.toml",
        "rerun/Cargo.lock",
    ],
    outs = ["bin/rerun"],
    cmd = """
        set -eu
        export HOME="$${HOME:-/tmp}"
        export PATH="$$HOME/.cargo/bin:/usr/local/cargo/bin:$$PATH"
        WS="$$(pwd)"
        if [[ -f "$$WS/simulation/web_monitor/scripts/build_viewer.sh" ]]; then
          ROOT="$$WS/simulation/web_monitor"
        else
          ROOT="$$WS"
        fi
        if [[ ! -x "$$ROOT/bin/rerun" ]]; then
          bash "$$ROOT/scripts/build_viewer.sh"
        fi
        cp -f "$$ROOT/bin/rerun" "$@"
        chmod +x "$@"
    """,
    local = 1,
    tags = [
        "local",
        "no-remote",
        "no-sandbox",
    ],
)

apollo_package()

cpplint()
