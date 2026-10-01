// Test utility: turn timestamped protobuf fixture files into a Cyber record.
#include <fstream>
#include <iomanip>
#include <iterator>
#include <set>
#include <stdexcept>
#include <string>
#include "cyber/record/record_writer.h"

int main(int argc, char** argv) {
  if (argc != 3) return 2;
  std::ifstream input(argv[1]);
  apollo::cyber::record::RecordWriter writer;
  writer.SetSizeOfFileSegmentation(0);
  writer.SetIntervalOfFileSegmentation(0);
  if (!input || !writer.Open(argv[2])) return 1;
  std::set<std::string> channels;
  uint64_t stamp;
  std::string channel, type, schema, payload;
  auto read=[](const std::string& path) {
    std::ifstream file(path, std::ios::binary);
    if (!file) throw std::runtime_error("Fixture file missing: " + path);
    return std::string(std::istreambuf_iterator<char>(file), {});
  };
  while (input >> stamp >> std::quoted(channel) >> std::quoted(type)
               >> std::quoted(schema) >> std::quoted(payload)) {
    if (channels.insert(channel).second &&
        !writer.WriteChannel(channel, type, read(schema))) return 1;
    if (!writer.WriteMessage(channel, read(payload), stamp)) return 1;
  }
  writer.Close();
  return input.eof() ? 0 : 1;
}
