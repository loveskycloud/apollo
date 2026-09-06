#include <sys/wait.h>
#include <unistd.h>

#include <cstdlib>
#include <iostream>
#include <string>

#include "gflags/gflags.h"

DEFINE_string(recording, "", "Optional .rrd/.rbl/.mcap path to open");
DEFINE_string(layout_dir, "", "Directory containing planner/perception/control .rbl");
DEFINE_int32(port, 9876, "gRPC listen port for SDKs / web proxy (Rerun 0.37+)");
// Kept for CLI compatibility with older scripts; ignored since 0.37 (WS merged into gRPC).
DEFINE_int32(ws_server_port, 9877, "DEPRECATED unused (0.37 uses gRPC on --port, not WebSocket)");
DEFINE_int32(web_viewer_port, 9090, "HTTP port for the web viewer UI");
DEFINE_bool(web_viewer, true, "Open/host the web viewer (requires web_viewer-enabled rerun binary)");
DEFINE_bool(native, false, "Force native desktop viewer (disables --web-viewer)");

namespace {

std::string ResolveRerunBinary() {
  if (const char* env = std::getenv("WEB_MONITOR_RERUN")) {
    return env;
  }
  const char* candidates[] = {
      "/apollo_workspace/simulation/web_monitor/bin/rerun",
      "/opt/apollo/neo/share/simulation/web_monitor/bin/rerun",
      "simulation/web_monitor/bin/rerun",
      "bin/rerun",
      "./rerun",
  };
  for (const char* path : candidates) {
    if (access(path, X_OK) == 0) {
      return path;
    }
  }
  return "rerun";
}

}  // namespace

int main(int argc, char** argv) {
  google::ParseCommandLineFlags(&argc, &argv, true);

  const std::string rerun = ResolveRerunBinary();
  std::string cmd;
  if (!FLAGS_layout_dir.empty()) {
    cmd += "AD_LAYOUT_DIR=\"";
    cmd += FLAGS_layout_dir;
    cmd += "\" ";
  } else {
    cmd += "AD_LAYOUT_DIR=\"/apollo_workspace/simulation/web_monitor/layouts\" ";
  }

  cmd += "\"";
  cmd += rerun;
  cmd += "\"";
  cmd += " --bind 0.0.0.0";
  cmd += " --port ";
  cmd += std::to_string(FLAGS_port);
  // Rerun 0.37+: no --ws-server-port. Viewer connects via
  //   http://HOST:WEB_PORT/?url=rerun+http://HOST:PORT/proxy
  if (FLAGS_ws_server_port != 9877) {
    std::cerr << "[web_monitor] warning: --ws_server_port is ignored on Rerun 0.37+ "
                 "(gRPC on --port replaces the old WebSocket port)\n";
  }

  const bool use_web = FLAGS_web_viewer && !FLAGS_native;
  if (use_web) {
    // --web-viewer implies --serve-web (HTTP UI + gRPC proxy)
    cmd += " --web-viewer --web-viewer-port ";
    cmd += std::to_string(FLAGS_web_viewer_port);
    cmd += " --hide-welcome-screen";
    // Large Apollo bags (~1GiB+) must stay in proxy history for late/slow web clients.
    cmd += " --server-memory-limit 8GiB";
  }

  if (!FLAGS_recording.empty()) {
    cmd += " \"";
    cmd += FLAGS_recording;
    cmd += "\"";
  }

  std::cout << "[web_monitor] exec: " << cmd << std::endl;
  const int ret = std::system(cmd.c_str());
  if (ret == -1) {
    std::cerr << "[web_monitor] failed to launch viewer" << std::endl;
    return 1;
  }
  return WEXITSTATUS(ret);
}
