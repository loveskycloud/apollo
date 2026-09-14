#ifndef SIMULATION_SIMULATOR_ENVIRONMENT_TOOLS_H_
#define SIMULATION_SIMULATOR_ENVIRONMENT_TOOLS_H_

#include <string>

namespace apollo {
namespace simulation {

// Read an explicitly configured width before the first HDMap load. Never use a
// hard-coded width or a protobuf default when the source is invalid.
bool ReadHalfVehicleWidth(const std::string& vehicle_config_path, double* width);
bool ApplyMapVehicleFlags(const std::string& map_dir,
                          const std::string& vehicle_config_path,
                          double half_vehicle_width);
bool VerifyMapVehicleFlags(const std::string& map_dir,
                           const std::string& vehicle_config_path,
                           double half_vehicle_width);

}  // namespace simulation
}  // namespace apollo
#endif  // SIMULATION_SIMULATOR_ENVIRONMENT_TOOLS_H_
