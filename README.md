# tasksetd
CPU affinity manager

Associate processes with CPUs to improve workload performance

Features:

* Continuously monitors list of processes and per-CPU load, adjusting CPU pinning accrodingly
* Understands CPU topology, supports Hyper-Threading (virtual cores)
* Initially designed for running Python multi-threaded application, allows to improve performance 40%+ under load
* Written in Python, easy to understand and modify
* Can be run under Docker and Kubernetes, as well as unmanaged program

Limitations:

* Currently can't assign more than 1 CPU per process

See also:

* K8S CPU Management Policies - https://kubernetes.io/docs/tasks/administer-cluster/cpu-management-policies/

Options:

```
  -c PROC_NAME_REGEX  Manage processes having this name (regex)
  -g PROC_GID         Manage processes having this GID (can be supplied multiple times)
```
