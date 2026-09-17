#include <arpa/inet.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <netinet/in.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cstdlib>
#include <iostream>
#include <string>

#include "gflags/gflags.h"

DEFINE_string(recording, "", "Optional .rrd/.rbl/.mcap path to open");
DEFINE_string(layout_dir, "", "Directory containing planner/perception/control .rbl");
DEFINE_int32(port, 9876, "gRPC listen port (Rerun 0.37+). Env: WEB_MONITOR_GRPC_PORT");
// Kept for CLI compatibility with older scripts; ignored since 0.37 (WS merged into gRPC).
DEFINE_int32(ws_server_port, 9877, "DEPRECATED unused (0.37 uses gRPC on --port, not WebSocket)");
DEFINE_int32(web_viewer_port, 9090, "HTTP port for the web viewer UI. Env: WEB_MONITOR_WEB_PORT");
DEFINE_bool(web_viewer, true, "Open/host the web viewer (requires web_viewer-enabled rerun binary)");
DEFINE_bool(native, false, "Force native desktop viewer (disables --web-viewer)");
// AD: public host the browser should use for the gRPC proxy. Empty = auto-detect.
// Set this when running behind a load balancer / reverse proxy so the printed URL uses
// the *public* host. Override per-user via ?grpc_host= in the browser.
DEFINE_string(grpc_host, "",
              "Public gRPC host (browser-visible). Empty = auto-detect from NIC. "
              "Env: WEB_MONITOR_GRPC_HOST. Override via ?grpc_host= in browser.");
// AD: public HTTP/web-viewer port the browser should use. 0 = same as --web_viewer_port.
// Useful when a reverse proxy maps a different external port to internal --web_viewer_port.
DEFINE_int32(web_port, 0,
             "Public web-viewer port (browser-visible). 0 = --web_viewer_port. "
             "Env: WEB_MONITOR_WEB_PORT");

namespace {

// AD: return the first non-loopback IPv4 address found on any interface,
// or empty string on failure.
std::string DetectFirstNonLoopbackIPv4() {
  struct ifaddrs* ifaddr = nullptr;
  if (getifaddrs(&ifaddr) != 0) {
    return "";
  }
  std::string result;
  for (struct ifaddrs* ifa = ifaddr; ifa != nullptr; ifa = ifa->ifa_next) {
    if (!ifa->ifa_addr || !(ifa->ifa_flags & IFF_UP)) {
      continue;
    }
    if (ifa->ifa_addr->sa_family != AF_INET) {
      continue;
    }
    char host[INET_ADDRSTRLEN] = {0};
    auto* sin = reinterpret_cast<struct sockaddr_in*>(ifa->ifa_addr);
    if (sin->sin_addr.s_addr == htonl(INADDR_LOOPBACK)) {
      continue;
    }
    if (inet_ntop(AF_INET, &sin->sin_addr, host, sizeof(host))) {
      result = host;
      break;
    }
  }
  freeifaddrs(ifaddr);
  return result;
}

std::string ResolveRerunBinary() {
  if (const char* env = std::getenv("WEB_MONITOR_RERUN")) {
    return env;
  }
  const char* candidates[] = {
      "/apollo_workspace/modules/simulation/web_monitor/bin/rerun",
      "/apollo_workspace/simulation/web_monitor/bin/rerun",
      "/opt/apollo/neo/share/modules/simulation/web_monitor/bin/rerun",
      "/opt/apollo/neo/share/simulation/web_monitor/bin/rerun",
      "modules/simulation/web_monitor/bin/rerun",
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

std::string DefaultLayoutDir() {
  const char* candidates[] = {
      "/apollo_workspace/modules/simulation/web_monitor/layouts",
      "/apollo_workspace/simulation/web_monitor/layouts",
      "/opt/apollo/neo/share/modules/simulation/web_monitor/layouts",
      "/opt/apollo/neo/share/simulation/web_monitor/layouts",
      "modules/simulation/web_monitor/layouts",
      "simulation/web_monitor/layouts",
  };
  for (const char* path : candidates) {
    if (access(path, R_OK) == 0) {
      return path;
    }
  }
  return "/apollo_workspace/modules/simulation/web_monitor/layouts";
}

}  // namespace

int main(int argc, char** argv) {
  google::ParseCommandLineFlags(&argc, &argv, true);

  // Env overrides — Phase 2: conf/web_monitor.conf will go here too.
  auto env_or_empty = [](const char* name) -> std::string {
    const char* v = std::getenv(name);
    return v ? std::string(v) : std::string();
  };
  // Only override from env if the flag was left at its default value.
  if (FLAGS_grpc_host.empty()) {
    std::string v = env_or_empty("WEB_MONITOR_GRPC_HOST");
    if (!v.empty()) {
      FLAGS_grpc_host = v;
    }
  }
  if (FLAGS_port == 9876) {
    std::string v = env_or_empty("WEB_MONITOR_GRPC_PORT");
    if (!v.empty()) {
      FLAGS_port = std::stoi(v);
    }
  }
  if (FLAGS_web_viewer_port == 9090) {
    std::string v = env_or_empty("WEB_MONITOR_WEB_PORT");
    if (!v.empty()) {
      FLAGS_web_viewer_port = std::stoi(v);
    }
  }

  // Detect public host: explicit flag > env > first non-loopback IPv4 (warn on miss).
  std::string public_host;
  if (!FLAGS_grpc_host.empty()) {
    public_host = FLAGS_grpc_host;
  } else {
    public_host = DetectFirstNonLoopbackIPv4();
    if (public_host.empty()) {
      std::cerr
          << "[web_monitor] WARNING: could not auto-detect a non-loopback IPv4.\n"
          << "[web_monitor]          Set --grpc_host=<ip-or-hostname> (or env\n"
          << "[web_monitor]          WEB_MONITOR_GRPC_HOST) so the printed URL\n"
          << "[web_monitor]          points at a host the browser can reach.\n"
          << "[web_monitor]          (If behind LB/reverse-proxy, set the proxy\n"
          << "[web_monitor]           host here.)\n";
      public_host = "127.0.0.1";
    }
  }
  const int public_web_port =
      (FLAGS_web_port > 0) ? FLAGS_web_port : FLAGS_web_viewer_port;
  const int public_grpc_port = FLAGS_port;

  const std::string rerun = ResolveRerunBinary();
  std::string cmd;
  if (!FLAGS_layout_dir.empty()) {
    cmd += "AD_LAYOUT_DIR=\"";
    cmd += FLAGS_layout_dir;
    cmd += "\" ";
  } else {
    cmd += "AD_LAYOUT_DIR=\"";
    cmd += DefaultLayoutDir();
    cmd += "\" ";
  }

  cmd += "\"";
  cmd += rerun;
  cmd += "\"";
  cmd += " --bind 0.0.0.0";
  cmd += " --port ";
  cmd += std::to_string(FLAGS_port);
  // Rerun 0.37+: no --ws-server-port. Viewer connects via
  //   http://HOST:WEB_PORT/  (index.html auto-fills ?url=rerun+http://HOST:PORT/proxy)
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
    // Browser origin is http://<LAN-IP>:9090, not localhost. Without this the
    // WASM client cannot fetch rerun+http://HOST:9876/proxy (Failed to fetch).
    cmd += " --cors-allow-origin http://* --cors-allow-origin http://*:*";
    cmd += " --cors-allow-origin https://* --cors-allow-origin https://*:*";
  }

  if (!FLAGS_recording.empty()) {
    cmd += " \"";
    cmd += FLAGS_recording;
    cmd += "\"";
  }

  std::cout << "[web_monitor] exec: " << cmd << std::endl;
  if (use_web) {
    std::cout << "[web_monitor] Open in browser:  http://" << public_host << ":"
              << public_web_port << "/\n"
              << "[web_monitor] (gRPC proxy on " << public_host << ":"
              << public_grpc_port
              << " -- override via ?grpc_host=&grpc_port=)" << std::endl;
  }
  const int ret = std::system(cmd.c_str());
  if (ret == -1) {
    std::cerr << "[web_monitor] failed to launch viewer" << std::endl;
    return 1;
  }
  return WEXITSTATUS(ret);
}
