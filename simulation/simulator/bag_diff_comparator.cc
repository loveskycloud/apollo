/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#include "simulation/simulator/bag_diff_comparator.h"

#include <fstream>
#include <sstream>

namespace apollo {
namespace simulation {

CompareResult BagDiffComparator::Compare(
    const std::vector<OutputRecord>& left,
    const std::vector<OutputRecord>& right) const {
  CompareResult result;
  result.left_count = left.size();
  result.right_count = right.size();
  if (left.size() != right.size()) {
    FieldDiff diff;
    diff.field_path = "count";
    diff.left = std::to_string(left.size());
    diff.right = std::to_string(right.size());
    result.diffs.push_back(diff);
    return result;
  }
  for (size_t i = 0; i < left.size(); ++i) {
    if (left[i].channel != right[i].channel ||
        left[i].sim_time_ns != right[i].sim_time_ns ||
        left[i].content != right[i].content) {
      FieldDiff diff;
      diff.field_path = "record[" + std::to_string(i) + "]";
      diff.left = left[i].channel;
      diff.right = right[i].channel;
      result.diffs.push_back(diff);
    }
  }
  if (result.diffs.empty()) {
    result.result = "PASS";
  }
  return result;
}

bool BagDiffComparator::WriteReport(const std::string& path,
                                    const CompareResult& result) const {
  std::ofstream ofs(path);
  if (!ofs) {
    return false;
  }
  ofs << "{\n";
  ofs << "  \"result\": \"" << result.result << "\",\n";
  ofs << "  \"left_count\": " << result.left_count << ",\n";
  ofs << "  \"right_count\": " << result.right_count << ",\n";
  ofs << "  \"diffs\": [\n";
  for (size_t i = 0; i < result.diffs.size(); ++i) {
    ofs << "    {\"field_path\": \"" << result.diffs[i].field_path << "\"}";
    if (i + 1 < result.diffs.size()) {
      ofs << ",";
    }
    ofs << "\n";
  }
  ofs << "  ]\n";
  ofs << "}\n";
  return true;
}

}  // namespace simulation
}  // namespace apollo
