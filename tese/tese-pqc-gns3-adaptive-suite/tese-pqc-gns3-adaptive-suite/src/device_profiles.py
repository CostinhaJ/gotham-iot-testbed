"""Device capability profiles for the adaptive-suite sweep.

Applied directly to the live pqc-client/pqc-server containers via the
Docker SDK's Container.update() (the same underlying mechanism as
`docker update --cpus/--memory`, the pattern already used in
../../../../Dockerfiles/benchmarks/stress.sh for CPU-limit sweeps --
there is no gns3utils.py wrapper for this, GNS3 only manages the
node/container lifecycle, not its cgroup limits, so this talks to the
`docker` Python SDK directly, same as run_benchmark_tese.py already
does for docker.from_env()).

No container restart needed -- `docker update` takes effect immediately
on a running container -- so this is the middle loop in
run_experiment_tese.py's sweep (cheaper than a network profile switch,
more expensive -- though still just a config write, no restart -- than
changing the rotation interval, which is only a sleep() duration in the
host-side controller).
"""

CPU_PERIOD_US = 100_000  # matches `docker update --cpus`'s internal period

# mem_limit is the docker-format string passed to Container.update();
# mem_limit_mb is the same value as a plain number, for CSV logging (see
# run_experiment_tese.py) -- kept separate so the dataset's
# device_mem_limit_mb column is actually numeric, not "128m"/"0".
DEVICE_PROFILES = {
    "constrained": {"cpus": 0.2, "mem_limit": "128m", "mem_limit_mb": 128},
    "typical": {"cpus": 0.5, "mem_limit": "256m", "mem_limit_mb": 256},
    "unconstrained": {"cpus": 0.0, "mem_limit": "0", "mem_limit_mb": None},  # 0 == no limit, same as `docker update --cpus 0 --memory 0`
}


def apply_device_profile(container, profile_name: str) -> None:
    """Apply a device capability profile to a live Docker container."""
    if profile_name not in DEVICE_PROFILES:
        raise ValueError(f"Unknown device profile '{profile_name}'. Known: {sorted(DEVICE_PROFILES)}")

    profile = DEVICE_PROFILES[profile_name]
    if profile["cpus"] > 0:
        cpu_quota = int(profile["cpus"] * CPU_PERIOD_US)
        container.update(cpu_period=CPU_PERIOD_US, cpu_quota=cpu_quota, mem_limit=profile["mem_limit"])
    else:
        container.update(cpu_period=CPU_PERIOD_US, cpu_quota=-1, mem_limit=profile["mem_limit"])
    print(f"[device_profiles] {container.name}: applied '{profile_name}' (cpus={profile['cpus']}, mem={profile['mem_limit']})")
