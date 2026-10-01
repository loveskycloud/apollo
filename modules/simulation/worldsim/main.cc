#include "modules/simulation/simulator/sim_runner.h"

// WorldSim and LogSim differ only in the task input adapter.
int main(int argc, char** argv) { return RunSimulator(argc, argv); }
