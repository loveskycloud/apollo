#!/usr/bin/env bash
set -Eeuo pipefail

APOLLO_NEO="${APOLLO_NEO:-/opt/apollo/neo}"
OUT_DIR="${OUT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/bin}"

mkdir -p "${OUT_DIR}"

g++ -std=c++17 -O2 -Wall -Wextra \
  -I"${APOLLO_NEO}/include" \
  -I"${APOLLO_NEO}/packages/3rd-glog/9.0.0-alpha3-r1/include" \
  -I"${APOLLO_NEO}/packages/3rd-gflags/9.0.0-alpha3-r1/include" \
  -I"${APOLLO_NEO}/packages/3rd-protobuf/latest/include" \
  "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/apollo_record_tool.cc" \
  -L"${APOLLO_NEO}/lib/cyber/record" \
  -L"${APOLLO_NEO}/lib/cyber/proto" \
  -L"${APOLLO_NEO}/lib/cyber/time" \
  -L"${APOLLO_NEO}/lib/cyber/message" \
  -L"${APOLLO_NEO}/lib/cyber/common" \
  -L"${APOLLO_NEO}/lib/cyber/base" \
  -L"${APOLLO_NEO}/lib/cyber" \
  -L"${APOLLO_NEO}/lib/3rd-protobuf" \
  -Wl,-rpath,"${APOLLO_NEO}/lib/cyber/record" \
  -Wl,-rpath,"${APOLLO_NEO}/lib/cyber/proto" \
  -Wl,-rpath,"${APOLLO_NEO}/lib/cyber/time" \
  -Wl,-rpath,"${APOLLO_NEO}/lib/cyber/message" \
  -Wl,-rpath,"${APOLLO_NEO}/lib/cyber/common" \
  -Wl,-rpath,"${APOLLO_NEO}/lib/cyber/base" \
  -Wl,-rpath,"${APOLLO_NEO}/lib/cyber" \
  -Wl,-rpath,"${APOLLO_NEO}/lib/3rd-protobuf" \
  -lcyber_record \
  -l_record_proto_cp_bin \
  -lcyber_time \
  -lcyber_message \
  -lcyber_common \
  -lcyber_binary \
  -lcyber_base \
  -L"${APOLLO_NEO}/lib/3rd-glog" \
  -lglog \
  -lprotobuf \
  -o "${OUT_DIR}/apollo_record_tool"

echo "Built ${OUT_DIR}/apollo_record_tool"
