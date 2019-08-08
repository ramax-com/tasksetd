#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from collections import defaultdict
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import time

PROC_STAT_RE = re.compile(r'^cpu(\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+)')
RESCAN_PERIOD = 2
JIFFIES = 100

def LOG(*params):
    logger = logging.getLogger()
    logger.info(*params)

class ProcessGone(IOError):
    def __init__(self, cause, *args, **kw):
        IOError.__init__(self, *args, **kw)
        self.cause = cause


class Process(object):
    STAT_START_TIME = 21

    def __init__(self, pid, proc_name, proc_groups, start_time):
        self.pid, self.proc_name, self.groups, self.start_time = pid, proc_name, proc_groups, start_time

    def __str__(self):
        return '%s[%s]/groups=%s' % (self.proc_name, self.pid, ','.join(str(gid) for gid in self.groups))

    @classmethod
    def filter_processes(cls, groups=None, proc_name_regex=None):
        for pid in os.listdir('/proc'):
            if pid.isdigit():
                try:
                    proc_name, proc_groups = cls._read_status(pid)
                    if (groups is None or proc_groups & groups) and \
                       (proc_name_regex is None or proc_name_regex.match(proc_name)):
                        process = cls(pid, proc_name, proc_groups, cls._read_start_time(pid))
                        yield process
                except ProcessGone:
                    continue

    @classmethod
    def _read_start_time(cls, pid):
        try:
            with open('/proc/%s/stat' % pid) as f:
                return f.read().split()[cls.STAT_START_TIME]
        except IOError as exc:
            raise ProcessGone(cause=exc)

    @classmethod
    def _read_cmdline(cls, pid):
        try:
            with open('/proc/%s/cmdline' % pid) as f:
                return f.read().split('\0')
        except IOError as exc:
            raise ProcessGone(cause=exc)


    @classmethod
    def _parse_status_file(cls, lines):
        groups = set()
        name = None
        for line in lines:
            k, v = line.rstrip('\n').split('\t', 1)
            if k == 'Name:':
                name = v
            if k == 'Gid:':
                egid = v.split('\t', 1)[0]
                groups.add(int(egid))
            elif line.startswith('Groups:'):
                for gid in v.split():
                    groups.add(int(gid))
                break
        return name, groups

    @classmethod
    def _read_status(cls, pid):
        try:
            with open('/proc/%s/status' % pid) as f:
                return cls._parse_status_file(f.readlines())
        except IOError as exc:
            raise ProcessGone(cause=exc)

    def __eq__(self, other):
        return (self.pid, self.start_time) == (other.pid, other.start_time)

    def is_running(self):
        try:
            proc_start_time = self._read_start_time(self.pid)
            # pid number can be reused, however the tuple (pid, start_time) is practically unique
            if proc_start_time != self.start_time:
                return False
        except ProcessGone:
            return False

        return True


class CPUData(object):
    TIME_RESOLUTION = 100.0
    HISTORY_DEPTH = 60

    def __init__(self):
        idle_times = self.get_idle_times()
        self.ncpus = len(idle_times)
        self.cpu_load_history = [(time.time(), idle_times)]
        self.cpu_load = [0] * self.ncpus
        self.idle_cpus = [0] * self.ncpus
        self.core_siblings = self.get_core_siblings()
        self.thread_siblings = self.get_thread_siblings()

    @staticmethod
    def _parse_ranges(ranges_str):
        siblings = set()
        for part in ranges_str.split(','):
            if '-' in part:
                start, end = part.split('-')
                siblings |= set(range(int(start), int(end)+1))
            else:
                siblings.add(int(part))
        return siblings

    def get_core_siblings(self):
        core_siblings = {}
        for cpu_n in range(self.ncpus):
            with open('/sys/devices/system/cpu/cpu%s/topology/core_siblings_list' % cpu_n) as f:
                siblings = self._parse_ranges(f.read())
                siblings.remove(cpu_n)
                core_siblings[cpu_n] = siblings
        return core_siblings

    def get_thread_siblings(self):
        thread_siblings = {}
        for cpu_n in range(self.ncpus):
            with open('/sys/devices/system/cpu/cpu%s/topology/thread_siblings_list' % cpu_n) as f:
                siblings = self._parse_ranges(f.read())
                siblings.remove(cpu_n)
                thread_siblings[cpu_n] = siblings
        return thread_siblings

    def get_idle_times(self):
        cpus = []
        with open('/proc/stat') as f:
            for line in f:
                m = PROC_STAT_RE.match(line)
                if m:
                    #cpu_n, user_time, nice_time, system_time, idle_time, iowait_time, irq_time, softirq_time = \
                    #    [int(n) for n in m.groups()]
                    #cpus.append(idle_time)
                    cpus.append(int(m.group(5)) / self.TIME_RESOLUTION)
        return cpus

    def record_cpu_load(self):
        self.cpu_load_history.append((time.time(), self.get_idle_times()))
        self.cpu_load, self.idle_cpus = self.get_cpu_load()

    def get_cpu_load(self):
        if len(self.cpu_load_history) > self.HISTORY_DEPTH:
            del self.cpu_load_history[0]

        start_t, first_row = self.cpu_load_history[0]
        end_t, last_row = self.cpu_load_history[-1]

        cpu_load = []
        idle_cpus = []
        for cpu_n in range(self.ncpus):
            clock_time = end_t - start_t
            idle_time = last_row[cpu_n] - first_row[cpu_n]
            cpu_load.append(float(clock_time - idle_time) / clock_time)
            idle_cpus.append((cpu_n, idle_time))

        return cpu_load, [cpu_n for cpu_n, idle_time in sorted(idle_cpus, key=lambda item: item[1])]


class CPU(object):
    def __init__(self, cpu_n, sibling_cores, sibling_threads, assigned_apps=None):
        self.n = cpu_n
        self.sibling_cores = sibling_cores
        self.sibling_threads = sibling_threads
        self.assigned = assigned_apps or []

    def assignment_factor(self):
        return len(self.assigned)

    def assign(self, app):
        self._set_cpu_affinity(app)
        self.assigned.append(app)

    def _set_cpu_affinity(self, app):
        output = subprocess.check_output(['/usr/bin/taskset', '-apc', str(self.n), str(app.pid)])
        logging.debug(output)

    def check_running_apps(self):
        deleted_pids = []
        for item_n in range(len(self.assigned)-1, -1, -1):
            app = self.assigned[item_n]
            if not app.is_running():
                LOG('App %s is gone, removing from process list' % app)
                deleted_pids.append(self.assigned[item_n].pid)
                del self.assigned[item_n]
        return deleted_pids


class AppScheduler(object):
    CPU_LOAD_WEIGHT = 4
    SIBLING_THREAD_INFLUENCE = 0.5
    SIBLING_CORE_INFLUENCE = 0.04  # small bias to distribute load across different physical CPUs
    REBALANCE_PERIOD = 60

    def __init__(self, cpu_data):
        self.cpu_data = cpu_data
        self.assigned_cpus = [CPU(cpu_n, cpu_data.core_siblings[cpu_n], cpu_data.thread_siblings[cpu_n])
                              for cpu_n in range(cpu_data.ncpus)]
        self.apps = []
        self.next_rebalance = 0

    # calculate CPU load score taking into account number of associated processes and current CPU utilization
    def get_cpu_load_score(self, cpu):
        cpu_load = self.cpu_data.cpu_load[cpu.n]
        # number of processes on sibling virtual core(s)
        sibling_thread_af = sum([self.assigned_cpus[cpu_n].assignment_factor() for cpu_n in cpu.sibling_threads])
        sibling_core_af = sum([self.assigned_cpus[cpu_n].assignment_factor() for cpu_n in cpu.sibling_cores])
        # utilization of sibling virtual core(s)
        if cpu.sibling_threads:
            sibling_thread_load = sum([self.cpu_data.cpu_load[cpu_n] for cpu_n in cpu.sibling_threads]) / len(cpu.sibling_threads)
        else:
            sibling_thread_load = 0

        # how loaded are other cores on the same physical CPU
        if cpu.sibling_cores:
            sibling_core_load = sum([self.cpu_data.cpu_load[cpu_n] for cpu_n in cpu.sibling_cores]) / len(cpu.sibling_cores)
        else:
            sibling_core_load = 0

        return (cpu.assignment_factor()
               + sibling_thread_af * self.SIBLING_THREAD_INFLUENCE
               + sibling_core_af * self.SIBLING_CORE_INFLUENCE) \
               + (cpu_load
                + sibling_thread_load * self.SIBLING_THREAD_INFLUENCE
                + sibling_core_load * self.SIBLING_CORE_INFLUENCE) * self.CPU_LOAD_WEIGHT

    def get_running_apps(self):
        apps = []
        for process in Process.filter_processes(Options.proc_gids, Options.proc_name_regex):
            apps.append(process)

        return apps

    def refresh_app_info(self):
        deleted_pids = []
        for cpu in self.assigned_cpus:
            deleted_pids += cpu.check_running_apps()
        return deleted_pids

    def get_free_cpu(self):
        cpus_by_load = sorted(self.assigned_cpus, key=lambda cpu: self.get_cpu_load_score(cpu))
        return cpus_by_load[0]

    def rebalance_cpus(self):
        while 1:
            cpus_by_load = sorted(self.assigned_cpus,
                                  key=lambda cpu: self.get_cpu_load_score(cpu))
            try:
                most_loaded_cpu = [cpu for cpu in reversed(cpus_by_load) if cpu.assigned][0]
            except IndexError:
                break
            least_loaded_cpu = cpus_by_load[0]
            if self.get_cpu_load_score(most_loaded_cpu) - self.get_cpu_load_score(least_loaded_cpu) < 2:
                break
            app = most_loaded_cpu.assigned.pop()
            LOG('Moving %s (%s) from CPU %s to %s' % (app.pid, app, most_loaded_cpu.n, least_loaded_cpu.n))
            least_loaded_cpu.assign(app)

    def process_changes(self):
        deleted_pids = self.refresh_app_info()
        running_apps = self.get_running_apps()
        new_apps = []
        for app in running_apps:
            if app not in self.apps:
                new_apps.append(app)
                free_cpu = self.get_free_cpu()
                logging.info('Assigning %s (%s) to CPU %s' % (app.pid, app, free_cpu.n))
                free_cpu.assign(app)

        if self.apps and self.next_rebalance <= time.time():
            self.next_rebalance = time.time() + self.REBALANCE_PERIOD
            self.rebalance_cpus()

        self.apps = running_apps

        if Options.args.debug:
            for cpu in self.assigned_cpus:
                logging.debug('%s %i%% %.03f [%s]', cpu.n, self.cpu_data.cpu_load[cpu.n] * 100, self.get_cpu_load_score(cpu),
                              ' '.join(str(p) for p in cpu.assigned))


class Options(object):
    @classmethod
    def parse_cmdline(cls):
        parser = argparse.ArgumentParser(description='Monitor process list and assign processes to physical CPU cores')
        parser.add_argument('-c', dest='proc_name_regex', help='Manage processes having this name (regex)',
                            action='store', required=False)
        parser.add_argument('-g', dest='proc_gid', help='Manage processes having this GID (can be supplied multiple times)',
                            action='append', required=False)
        parser.add_argument('-q', '--quiet', dest='quiet', help='Quiet mode',
                            action='store_true', default=False)
        parser.add_argument('-d', '--debug', dest='debug', help='Output debug messages',
                            action='store_true', default=False)
        args = parser.parse_args()
        cls.args = args
        cls.proc_name_regex = re.compile(args.proc_name_regex) if args.proc_name_regex else None
        cls.proc_gids = {int(gid) for gid in args.proc_gid} if args.proc_gid else None

    @classmethod
    def match_proc_name(cls, process):
        try:
            m = cls.proc_name_regex.match(process.proc_name)
            return bool(m)
        except ProcessGone:
            return False

    @classmethod
    def match_group(cls, process):
        return bool(process.groups & cls.proc_gids)


def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG if Options.args.debug else logging.INFO)
    if not Options.args.quiet:
        logger.addHandler(logging.StreamHandler())


def main():
    Options.parse_cmdline()
    setup_logging()
    cpu_data = CPUData()
    app_scheduler = AppScheduler(cpu_data)
    while 1:
        cpu_data.record_cpu_load()
        time.sleep(RESCAN_PERIOD)
        try:
            app_scheduler.process_changes()
        except Exception:
            logging.exception('Exception in app_scheduler')


if __name__ == '__main__':
    main()

