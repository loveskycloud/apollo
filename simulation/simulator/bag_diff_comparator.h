/******************************************************************************
 * Copyright 2026 The Apollo Authors. All Rights Reserved.
 *****************************************************************************/

#ifndef SIMULATION_SIMULATOR_BAG_DIFF_COMPARATOR_H_
#define SIMULATION_SIMULATOR_BAG_DIFF_COMPARATOR_H_

#include <string>
#include <vector>

#include "modules/simulation/simulator/result_sink.h"

namespace apollo {
namespace simulation {

struct FieldDiff {
  std::string field_path;
  std::string left;
  std::string right;
};

struct CompareResult {
  std::string result = "FAIL";
  size_t left_count = 0;
  size_t right_count = 0;
  std::vector<FieldDiff> diffs;
};

class BagDiffComparator {
 public:
  CompareResult Compare(const std::vector<OutputRecord>& left,
                        const std::vector<OutputRecord>& right) const;
  bool WriteReport(const std::string& path, const CompareResult& result) const;
};

}  // namespace simulation
}  // namespace apollo

#endif  // SIMULATION_SIMULATOR_BAG_DIFF_COMPARATOR_H_
