#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <set>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "cyber/record/record_message.h"
#include "cyber/record/record_reader.h"
#include "cyber/record/record_writer.h"

namespace fs = std::filesystem;

using apollo::cyber::record::RecordMessage;
using apollo::cyber::record::RecordReader;
using apollo::cyber::record::RecordWriter;

namespace {

struct Options {
  std::string command;
  std::vector<std::string> inputs;
  std::string output;
  std::string output_dir;
  uint64_t chunk_ms = 1000;
  uint64_t begin_ns = 0;
  uint64_t duration_ms = 0;
  std::set<std::string> include_channels;
  std::set<std::string> exclude_channels;
};

std::string JsonEscape(const std::string& input) {
  std::ostringstream out;
  for (const unsigned char c : input) {
    switch (c) {
      case '"':
        out << "\\\"";
        break;
      case '\\':
        out << "\\\\";
        break;
      case '\b':
        out << "\\b";
        break;
      case '\f':
        out << "\\f";
        break;
      case '\n':
        out << "\\n";
        break;
      case '\r':
        out << "\\r";
        break;
      case '\t':
        out << "\\t";
        break;
      default:
        if (c < 0x20) {
          out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
              << static_cast<int>(c);
        } else {
          out << c;
        }
    }
  }
  return out.str();
}

void Usage() {
  std::cerr
      << "Usage:\n"
      << "  apollo_record_tool index -o index.jsonl <record...>\n"
      << "  apollo_record_tool split --chunk-ms 1000 --output-dir chunks <record...>\n"
      << "  apollo_record_tool slice --begin-ns T --duration-ms 1000 -o out.record <record...>\n"
      << "  apollo_record_tool dump-jsonl [-o out.jsonl] [--begin-ns T --duration-ms N] <record...>\n"
      << "\nOptions:\n"
      << "  -o, --output FILE        JSONL index output for index command\n"
      << "                           record output for slice command\n"
      << "  --output-dir DIR         Output directory for split command\n"
      << "  --chunk-ms N             Split size in milliseconds, default 1000\n"
      << "  --begin-ns N             Slice begin timestamp in nanoseconds\n"
      << "  --duration-ms N          Slice duration in milliseconds, default 1000\n"
      << "  -c, --channel NAME       Include only this channel, repeatable\n"
      << "  -k, --skip-channel NAME  Exclude this channel, repeatable\n";
}

bool ParseArgs(int argc, char** argv, Options* options) {
  if (argc < 2) {
    return false;
  }
  options->command = argv[1];
  for (int i = 2; i < argc; ++i) {
    const std::string arg = argv[i];
    auto need_value = [&](const std::string& name) -> const char* {
      if (i + 1 >= argc) {
        std::cerr << "Missing value for " << name << "\n";
        return nullptr;
      }
      return argv[++i];
    };

    if (arg == "-o" || arg == "--output") {
      const char* value = need_value(arg);
      if (value == nullptr) return false;
      options->output = value;
    } else if (arg == "--output-dir") {
      const char* value = need_value(arg);
      if (value == nullptr) return false;
      options->output_dir = value;
    } else if (arg == "--chunk-ms") {
      const char* value = need_value(arg);
      if (value == nullptr) return false;
      options->chunk_ms = std::stoull(value);
    } else if (arg == "--begin-ns") {
      const char* value = need_value(arg);
      if (value == nullptr) return false;
      options->begin_ns = std::stoull(value);
    } else if (arg == "--duration-ms") {
      const char* value = need_value(arg);
      if (value == nullptr) return false;
      options->duration_ms = std::stoull(value);
    } else if (arg == "-c" || arg == "--channel") {
      const char* value = need_value(arg);
      if (value == nullptr) return false;
      options->include_channels.insert(value);
    } else if (arg == "-k" || arg == "--skip-channel") {
      const char* value = need_value(arg);
      if (value == nullptr) return false;
      options->exclude_channels.insert(value);
    } else if (arg == "-h" || arg == "--help") {
      return false;
    } else {
      options->inputs.push_back(arg);
    }
  }

  if (options->command != "index" && options->command != "split" &&
      options->command != "slice" && options->command != "prefix" && options->command != "header" && options->command != "dump-jsonl") {
    std::cerr << "Unknown command: " << options->command << "\n";
    return false;
  }
  if (options->inputs.empty()) {
    std::cerr << "At least one record file is required.\n";
    return false;
  }
  if (options->command == "index" && options->output.empty()) {
    std::cerr << "index requires -o/--output.\n";
    return false;
  }
  if (options->command == "slice" &&
      (options->output.empty() || options->begin_ns == 0 ||
       options->duration_ms == 0)) {
    std::cerr << "slice requires -o/--output, --begin-ns and --duration-ms.\n";
    return false;
  }
  if (options->command == "split" && options->output_dir.empty()) {
    std::cerr << "split requires --output-dir.\n";
    return false;
  }
  if (options->chunk_ms == 0) {
    std::cerr << "--chunk-ms must be > 0.\n";
    return false;
  }
  return true;
}

std::string Base64Encode(const std::string& input) {
  static constexpr char table[] =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string output;
  output.reserve(((input.size() + 2) / 3) * 4);
  uint32_t value = 0;
  int bits = -6;
  for (const unsigned char c : input) {
    value = (value << 8) + c;
    bits += 8;
    while (bits >= 0) {
      output.push_back(table[(value >> bits) & 0x3F]);
      bits -= 6;
    }
  }
  if (bits > -6) {
    output.push_back(table[((value << 8) >> (bits + 8)) & 0x3F]);
  }
  while (output.size() % 4) {
    output.push_back('=');
  }
  return output;
}

bool KeepChannel(const Options& options, const std::string& channel) {
  if (!options.include_channels.empty() &&
      options.include_channels.count(channel) == 0) {
    return false;
  }
  if (options.exclude_channels.count(channel) != 0) {
    return false;
  }
  return true;
}

struct ChannelMeta {
  std::string type;
  std::string proto_desc;
};

std::unordered_map<std::string, ChannelMeta> ReadChannelMeta(
    const std::vector<std::string>& inputs) {
  std::unordered_map<std::string, ChannelMeta> meta;
  for (const auto& input : inputs) {
    RecordReader reader(input);
    if (!reader.IsValid()) {
      continue;
    }
    for (const auto& channel : reader.GetChannelList()) {
      if (meta.count(channel) != 0) {
        continue;
      }
      meta[channel] = ChannelMeta{reader.GetMessageType(channel),
                                  reader.GetProtoDesc(channel)};
    }
  }
  return meta;
}

int RunIndex(const Options& options) {
  std::ofstream out(options.output);
  if (!out) {
    std::cerr << "Failed to open output: " << options.output << "\n";
    return 1;
  }

  uint64_t global_index = 0;
  uint64_t begin_time = 0;
  uint64_t end_time = 0;
  std::unordered_map<std::string, uint64_t> channel_counts;

  for (const auto& input : options.inputs) {
    RecordReader reader(input);
    if (!reader.IsValid()) {
      std::cerr << "Invalid record: " << input << "\n";
      return 1;
    }

    RecordMessage message;
    uint64_t file_index = 0;
    while (reader.ReadMessage(&message)) {
      if (!KeepChannel(options, message.channel_name)) {
        continue;
      }
      if (begin_time == 0 || message.time < begin_time) {
        begin_time = message.time;
      }
      if (message.time > end_time) {
        end_time = message.time;
      }
      ++channel_counts[message.channel_name];

      out << "{\"i\":" << global_index << ",\"file_i\":" << file_index
          << ",\"timestamp_ns\":" << message.time << ",\"file\":\""
          << JsonEscape(input) << "\",\"channel\":\""
          << JsonEscape(message.channel_name) << "\",\"type\":\""
          << JsonEscape(reader.GetMessageType(message.channel_name))
          << "\",\"size\":" << message.content.size() << "}\n";
      ++global_index;
      ++file_index;
    }
  }

  const fs::path summary_path = fs::path(options.output).concat(".summary.json");
  std::ofstream summary(summary_path);
  summary << "{\n";
  summary << "  \"message_count\": " << global_index << ",\n";
  summary << "  \"begin_time_ns\": " << begin_time << ",\n";
  summary << "  \"end_time_ns\": " << end_time << ",\n";
  summary << "  \"duration_sec\": "
          << std::fixed << std::setprecision(9)
          << (end_time > begin_time ? (end_time - begin_time) / 1e9 : 0.0)
          << ",\n";
  summary << "  \"channels\": {\n";
  bool first = true;
  for (const auto& [channel, count] : channel_counts) {
    if (!first) summary << ",\n";
    first = false;
    summary << "    \"" << JsonEscape(channel) << "\": " << count;
  }
  summary << "\n  }\n";
  summary << "}\n";

  std::cerr << "Wrote index: " << options.output << "\n";
  std::cerr << "Wrote summary: " << summary_path << "\n";
  return 0;
}

std::string ChunkPath(const std::string& output_dir, uint64_t chunk_id,
                      uint64_t chunk_begin_ns) {
  std::ostringstream name;
  name << "chunk_" << std::setw(8) << std::setfill('0') << chunk_id << "_"
       << chunk_begin_ns << ".record";
  return (fs::path(output_dir) / name.str()).string();
}

int RunSplit(const Options& options) {
  fs::create_directories(options.output_dir);

  const auto meta = ReadChannelMeta(options.inputs);
  const uint64_t chunk_ns = options.chunk_ms * 1000ULL * 1000ULL;
  uint64_t first_time = 0;
  uint64_t current_chunk = std::numeric_limits<uint64_t>::max();
  uint64_t chunk_count = 0;
  uint64_t message_count = 0;
  std::unique_ptr<RecordWriter> writer;
  std::unordered_set<std::string> writer_channels;
  std::ofstream manifest(fs::path(options.output_dir) / "manifest.jsonl");

  auto open_chunk = [&](uint64_t chunk_id, uint64_t chunk_begin_ns) -> bool {
    if (writer) {
      writer->Close();
    }
    writer = std::make_unique<RecordWriter>();
    writer->SetSizeOfFileSegmentation(0);
    writer->SetIntervalOfFileSegmentation(0);
    writer_channels.clear();

    const auto path = ChunkPath(options.output_dir, chunk_id, chunk_begin_ns);
    if (!writer->Open(path)) {
      std::cerr << "Failed to open chunk: " << path << "\n";
      return false;
    }
    manifest << "{\"chunk\":" << chunk_id << ",\"begin_time_ns\":"
             << chunk_begin_ns << ",\"path\":\"" << JsonEscape(path)
             << "\"}\n";
    ++chunk_count;
    return true;
  };

  for (const auto& input : options.inputs) {
    RecordReader reader(input);
    if (!reader.IsValid()) {
      std::cerr << "Invalid record: " << input << "\n";
      return 1;
    }

    RecordMessage message;
    while (reader.ReadMessage(&message)) {
      if (!KeepChannel(options, message.channel_name)) {
        continue;
      }
      if (first_time == 0) {
        first_time = message.time;
      }
      const uint64_t chunk_id = (message.time - first_time) / chunk_ns;
      const uint64_t chunk_begin_ns = first_time + chunk_id * chunk_ns;
      if (chunk_id != current_chunk) {
        current_chunk = chunk_id;
        if (!open_chunk(chunk_id, chunk_begin_ns)) {
          return 1;
        }
      }

      if (writer_channels.count(message.channel_name) == 0) {
        const auto it = meta.find(message.channel_name);
        if (it == meta.end()) {
          std::cerr << "Missing channel metadata: " << message.channel_name
                    << "\n";
          return 1;
        }
        writer->WriteChannel(message.channel_name, it->second.type,
                             it->second.proto_desc);
        writer_channels.insert(message.channel_name);
      }
      if (!writer->WriteMessage(message.channel_name, message.content,
                                message.time)) {
        std::cerr << "Failed to write message for channel "
                  << message.channel_name << "\n";
        return 1;
      }
      ++message_count;
    }
  }

  if (writer) {
    writer->Close();
  }

  std::cerr << "Wrote " << chunk_count << " chunks and " << message_count
            << " messages to " << options.output_dir << "\n";
  return 0;
}

int RunSlice(const Options& options) {
  const auto meta = ReadChannelMeta(options.inputs);
  const uint64_t end_ns =
      options.begin_ns + options.duration_ms * 1000ULL * 1000ULL;

  RecordWriter writer;
  writer.SetSizeOfFileSegmentation(0);
  writer.SetIntervalOfFileSegmentation(0);
  if (!writer.Open(options.output)) {
    std::cerr << "Failed to open output: " << options.output << "\n";
    return 1;
  }

  std::unordered_set<std::string> writer_channels;
  uint64_t message_count = 0;
  for (const auto& input : options.inputs) {
    RecordReader reader(input);
    if (!reader.IsValid()) {
      std::cerr << "Invalid record: " << input << "\n";
      return 1;
    }

    RecordMessage message;
    while (reader.ReadMessage(&message, options.begin_ns, end_ns)) {
      if (!KeepChannel(options, message.channel_name)) {
        continue;
      }
      if (writer_channels.count(message.channel_name) == 0) {
        const auto it = meta.find(message.channel_name);
        if (it == meta.end()) {
          std::cerr << "Missing channel metadata: " << message.channel_name
                    << "\n";
          return 1;
        }
        writer.WriteChannel(message.channel_name, it->second.type,
                            it->second.proto_desc);
        writer_channels.insert(message.channel_name);
      }
      if (!writer.WriteMessage(message.channel_name, message.content,
                               message.time)) {
        std::cerr << "Failed to write message for channel "
                  << message.channel_name << "\n";
        return 1;
      }
      ++message_count;
    }
  }
  writer.Close();

  std::cerr << "Wrote " << message_count << " messages to " << options.output
            << "\n";
  return 0;
}

int RunHeader(const Options& options) {
  if (options.inputs.size() != 1) return 1;
  apollo::cyber::record::RecordFileReader reader;
  if (!reader.Open(options.inputs.front())) return 1;
  const auto& header = reader.GetHeader();
  std::cout << "{\"begin_ns\":\"" << header.begin_time()
            << "\",\"end_ns\":\"" << header.end_time()
            << "\",\"messages\":" << header.message_number() << "}\n";
  return 0;
}

// Extract one fully received Cyber chunk using the existing Cyber protobuf reader.
// This accepts a growing source: it never reads its not-yet-arrived index/tail.
int RunPrefix(const Options& options) {
  if (options.inputs.size() != 1 || options.output.empty()) {
    std::cerr << "prefix requires one source and -o output.record\n";
    return 1;
  }
  apollo::cyber::record::RecordFileReader reader;
  if (!reader.Open(options.inputs.front())) return 1;
  if (reader.GetHeader().compress() != apollo::cyber::proto::COMPRESS_NONE) {
    std::cerr << "Compressed Cyber sections are unsupported\n";
    return 1;
  }
  RecordWriter writer;
  writer.SetSizeOfFileSegmentation(0);
  writer.SetIntervalOfFileSegmentation(0);
  if (!writer.Open(options.output)) return 1;
  std::unordered_set<std::string> channels;
  apollo::cyber::record::Section section;
  while (reader.ReadSection(&section)) {
    if (section.type == apollo::cyber::proto::SECTION_CHANNEL) {
      apollo::cyber::proto::Channel channel;
      if (!reader.ReadSection(section.size, &channel) ||
          !writer.WriteChannel(channel.name(), channel.message_type(), channel.proto_desc())) {
        return 1;
      }
      channels.insert(channel.name());
    } else if (section.type == apollo::cyber::proto::SECTION_CHUNK_BODY) {
      apollo::cyber::proto::ChunkBody chunk;
      if (!reader.ReadSection(section.size, &chunk) || chunk.messages().empty()) return 1;
      for (const auto& message : chunk.messages()) {
        if (!channels.count(message.channel_name()) ||
            !writer.WriteMessage(message.channel_name(), message.content(), message.time())) {
          std::cerr << "Invalid first record chunk\n";
          return 1;
        }
      }
      writer.Close();
      return 0;
    } else if (section.type == apollo::cyber::proto::SECTION_CHUNK_HEADER) {
      if (!reader.SkipSection(section.size)) return 1;
    } else {
      std::cerr << "No complete first chunk in source\n";
      return 1;
    }
  }
  return 1;
}

int RunDumpJsonl(const Options& options) {
  std::unique_ptr<std::ofstream> file_out;
  std::ostream* out = &std::cout;
  if (!options.output.empty() && options.output != "-") {
    file_out = std::make_unique<std::ofstream>(options.output);
    if (!*file_out) {
      std::cerr << "Failed to open output: " << options.output << "\n";
      return 1;
    }
    out = file_out.get();
  }

  const uint64_t begin_ns = options.begin_ns;
  const uint64_t end_ns =
      options.duration_ms == 0
          ? std::numeric_limits<uint64_t>::max()
          : begin_ns + options.duration_ms * 1000ULL * 1000ULL;
  std::unordered_set<std::string> emitted_schemas;
  uint64_t message_count = 0;
  bool meta_emitted = false;

  for (const auto& input : options.inputs) {
    RecordReader reader(input);
    if (!reader.IsValid()) {
      std::cerr << "Invalid record: " << input << "\n";
      return 1;
    }

    if (!meta_emitted) {
      const auto& header = reader.GetHeader();
      uint64_t meta_begin = options.begin_ns != 0 ? options.begin_ns : header.begin_time();
      uint64_t meta_end = header.end_time();
      if (options.duration_ms != 0) {
        meta_end = meta_begin + options.duration_ms * 1000ULL * 1000ULL;
        if (header.end_time() != 0 && meta_end > header.end_time()) {
          meta_end = header.end_time();
        }
      }
      (*out) << "{\"op\":\"meta\",\"begin_ns\":" << meta_begin
             << ",\"end_ns\":" << meta_end
             << ",\"source\":\"" << JsonEscape(input) << "\"}\n";
      meta_emitted = true;
    }


    // Emit schemas for every channel up front (incl. empty channels) so MCAP
    // topic lists match the Apollo bag header exactly.
    for (const auto& channel : reader.GetChannelList()) {
      if (!KeepChannel(options, channel)) {
        continue;
      }
      if (emitted_schemas.count(channel) != 0) {
        continue;
      }
      (*out) << "{\"op\":\"schema\",\"channel\":\""
             << JsonEscape(channel) << "\",\"type\":\""
             << JsonEscape(reader.GetMessageType(channel))
             << "\",\"proto_desc_b64\":\""
             << Base64Encode(reader.GetProtoDesc(channel)) << "\"}\n";
      emitted_schemas.insert(channel);
    }

    RecordMessage message;
    while (options.begin_ns == 0 && options.duration_ms == 0
               ? reader.ReadMessage(&message)
               : reader.ReadMessage(&message, begin_ns, end_ns)) {
      if (!KeepChannel(options, message.channel_name)) {
        continue;
      }
      if (emitted_schemas.count(message.channel_name) == 0) {
        (*out) << "{\"op\":\"schema\",\"channel\":\""
               << JsonEscape(message.channel_name) << "\",\"type\":\""
               << JsonEscape(reader.GetMessageType(message.channel_name))
               << "\",\"proto_desc_b64\":\""
               << Base64Encode(reader.GetProtoDesc(message.channel_name))
               << "\"}\n";
        emitted_schemas.insert(message.channel_name);
      }
      (*out) << "{\"op\":\"msg\",\"timestamp_ns\":" << message.time
             << ",\"channel\":\"" << JsonEscape(message.channel_name)
             << "\",\"data_b64\":\"" << Base64Encode(message.content)
             << "\"}\n";
      ++message_count;
    }
  }

  std::cerr << "Dumped " << message_count << " messages\n";
  return 0;
}

}  // namespace

int main(int argc, char** argv) {
  Options options;
  if (!ParseArgs(argc, argv, &options)) {
    Usage();
    return 2;
  }

  if (options.command == "header") return RunHeader(options);
  if (options.command == "prefix") return RunPrefix(options);
  if (options.command == "index") {
    return RunIndex(options);
  }
  if (options.command == "split") {
    return RunSplit(options);
  }
  if (options.command == "slice") {
    return RunSlice(options);
  }
  if (options.command == "dump-jsonl") {
    return RunDumpJsonl(options);
  }
  return 2;
}
