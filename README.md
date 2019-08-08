# tasksetd
CPU affinity for k8s pods

Associate processes with CPUs to improve workload performance

Features:

* Continuously monitors list of processes and CPU load and adjusts CPU pinning
* Initially designed for running python multi-threaded application, allows to improve performance 40%+ under load
* Understands CPU topology, supports Hyper-Threading (virtual cores)
* Written in Python, easy to understand and modify
* Can be run under Docker and Kubernetes
