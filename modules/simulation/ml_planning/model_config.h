#pragma once
#include <filesystem>
#include <regex>
#include <stdexcept>
#include <string>

namespace apollo::simulation::ml {
inline std::string ResolveModelWeights(const std::string& config_path,
                                       const std::string& version) {
  if(!std::regex_match(version,std::regex("v[0-9]+([._-][A-Za-z0-9]+)*")))
    throw std::runtime_error("Invalid model_version: " + version);
  const auto config=std::filesystem::canonical(config_path);
  const auto weights=config.parent_path().parent_path()/"models"/version/"unified.weights";
  if(!std::filesystem::is_regular_file(weights))
    throw std::runtime_error("Configured model weights do not exist: " + weights.string());
  return std::filesystem::canonical(weights).string();
}
}  // namespace apollo::simulation::ml
