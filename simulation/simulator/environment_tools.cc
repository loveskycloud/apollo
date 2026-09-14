#include "simulation/simulator/environment_tools.h"

#include <cmath>

#include "cyber/common/file.h"
#include "cyber/common/log.h"
#include "modules/common/configs/config_gflags.h"
#include "modules/common_msgs/config_msgs/vehicle_config.pb.h"

namespace apollo {
namespace simulation {

bool ReadHalfVehicleWidth(const std::string& path, double* width) {
  common::VehicleConfig config;
  if (width == nullptr || !cyber::common::GetProtoFromFile(path, &config) ||
      !config.has_vehicle_param() || !config.vehicle_param().has_width() ||
      !std::isfinite(config.vehicle_param().width()) ||
      config.vehicle_param().width() <= 0.0) {
    AERROR << "Invalid or missing vehicle width: " << path;
    return false;
  }
  *width = config.vehicle_param().width() / 2.0;
  return true;
}

bool ApplyMapVehicleFlags(const std::string& map_dir,
                          const std::string& vehicle_config_path,
                          double half_vehicle_width) {
  if (map_dir.empty() || vehicle_config_path.empty() ||
      !std::isfinite(half_vehicle_width) || half_vehicle_width <= 0.0) {
    AERROR << "Invalid simulation map/vehicle flags";
    return false;
  }
  FLAGS_map_dir = map_dir;
  FLAGS_vehicle_config_path = vehicle_config_path;
  FLAGS_half_vehicle_width = half_vehicle_width;
  return VerifyMapVehicleFlags(map_dir, vehicle_config_path, half_vehicle_width);
}

bool VerifyMapVehicleFlags(const std::string& map_dir,
                           const std::string& vehicle_config_path,
                           double half_vehicle_width) {
  if (FLAGS_map_dir != map_dir || FLAGS_vehicle_config_path != vehicle_config_path ||
      FLAGS_half_vehicle_width != half_vehicle_width) {
    AERROR << "Simulation environment overwritten by module configuration: map_dir="
           << FLAGS_map_dir << " vehicle_config_path=" << FLAGS_vehicle_config_path
           << " half_vehicle_width=" << FLAGS_half_vehicle_width;
    return false;
  }
  AINFO << "Simulation environment verified: map_dir=" << FLAGS_map_dir
        << " vehicle_config_path=" << FLAGS_vehicle_config_path
        << " half_vehicle_width=" << FLAGS_half_vehicle_width;
  return true;
}

}  // namespace simulation
}  // namespace apollo
