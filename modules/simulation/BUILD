load("//tools:apollo_package.bzl", "apollo_package")
load("//tools/install:install.bzl", "install_src_files")

package(default_visibility = ["//visibility:public"])

# Install C++ packages individually. Recursively copying this umbrella directory
# also dereferences task workspaces and copies the Rust target/cache trees.
install_src_files(
    name = "install_src",
    deps = [
        "//modules/simulation/fake_prediction:install_src",
        "//modules/simulation/logsim:install_src",
        "//modules/simulation/ml_planning:install_src",
        "//modules/simulation/simulator:install_src",
        "//modules/simulation/worldsim:install_src",
    ],
    type = "neo",
)

apollo_package()
