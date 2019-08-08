# -*- coding: utf-8 -*-

import mock
from tasksetd import CPUData, AppScheduler, ProcessGone, Process, CPU

class CPUData_(CPUData):
    def __init__(self):
        self.cpu_load_history = []
        self.ncpus = 4
        self.history_depth = 60
        self.cpu_load = [0] * self.ncpus
        self.thread_siblings = {0: {2}, 1: {3}, 2: {0}, 3: {1}}
        self.core_siblings = {0: {1,2,3}, 1: {0,2,3}, 2: {0,1,3}, 3: {0,1,2}}

    def get_current_load(self):
        return [0, 0, 0, 0]

def test_get_idle_cpu():
    cpuset = CPUData_()
    cpuset.cpu_load_history = [(100, [1.0, 2.0, 5.0, 0.0]), (102, [2.0, 3.0, 5.0, 1.0])]
    cpuset.ncpus = 4

    cpu_load, idle_cpus = cpuset.get_cpu_load()
    assert idle_cpus[0] == 2
    assert cpu_load == [0.5, 0.5, 1.0, 0.5]


def GET_MOCK_CPUS():
    assigned_cpus = [
        CPU(0, [], [], [1,2,3,4]),
        CPU(1, [], [], [5,6]),
        CPU(2, [], [], [7]),
        CPU(3, [], [], [8,9]),
    ]
    for cpu in assigned_cpus:
        for n, pid in enumerate(cpu.assigned):
            cpu.assigned[n] = Process(pid=pid, start_time=0, proc_groups={6100}, proc_name='app')
    return assigned_cpus


def test_find_cpu_with_least_processes():
    assigned_cpus = GET_MOCK_CPUS()

    a_s = AppScheduler(CPUData_())
    a_s.assigned_cpus = assigned_cpus

    cpu = a_s.get_free_cpu()
    assert cpu.n == 2


def test_remove_exited_app():
    assigned_cpus = GET_MOCK_CPUS()
    a_s = AppScheduler(CPUData_())
    a_s.assigned_cpus = assigned_cpus
    with mock.patch('tasksetd.Process.is_running', return_value=True):
        a_s.refresh_app_info()
        assert len(assigned_cpus[0].assigned) == 4

    with mock.patch('tasksetd.Process.is_running', return_value=False):
        a_s.refresh_app_info()
        assert len(assigned_cpus[0].assigned) == 0


@mock.patch('tasksetd.CPU._set_cpu_affinity')
def test_rebalance_cpus(*args):
    assigned_cpus = GET_MOCK_CPUS()
    a_s = AppScheduler(CPUData_())
    a_s.assigned_cpus = assigned_cpus
    a_s.rebalance_cpus()

    assert len(assigned_cpus[2].assigned) >= 2
    assert len(assigned_cpus[0].assigned) <= 3

    assigned_cpus[0].assigned = []
    a_s.rebalance_cpus()
    assert len(assigned_cpus[0].assigned) == 1


def test_process_gone():
    cause = ValueError('aaa')
    try:
        raise ProcessGone(cause=cause)
    except ProcessGone as exc:
        assert exc.cause == cause


_PROC_STATUS = """\
Name:	bash
State:	S (sleeping)
Tgid:	14634
Ngid:	0
Pid:	14634
PPid:	14633
TracerPid:	0
Uid:	1000	1000	1000	1000
Gid:	1000	1000	1000	1000
FDSize:	256
Groups:	4 24 27 30 46 110 115 116 1000
"""

def test_read_gids(*args):
    name, gids = Process._parse_status_file(_PROC_STATUS.split('\n'))
    assert gids == {4,24,27,30,46,110,115,116,1000}


def test_parse_ranges():
    assert CPUData._parse_ranges("1") == {1}
    assert CPUData._parse_ranges("1,3") == {1,3}
    assert CPUData._parse_ranges("1-3") == {1,2,3}
    assert CPUData._parse_ranges("1-3,6-8") == {1,2,3,6,7,8}
