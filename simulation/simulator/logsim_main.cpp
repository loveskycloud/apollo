#include "modules/simulation/simulator/sim_runner.h"

// Compatibility entry point. Input kind belongs to task.pb.txt, not the binary.
int main(int argc, char** argv) { return RunSimulator(argc, argv); }
