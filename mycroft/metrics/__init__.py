# Copyright 2017 Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import json
from queue import Queue, Empty
import threading
import time

import requests

from ovos_backend_client.api import MetricsApi
from ovos_config.config import Configuration
from ovos_bus_client.session import SessionManager
from ovos_utils.log import LOG
from ovos_core.version import OVOS_VERSION_STR
from copy import copy
# backwards compat import
from ovos_utils.metrics import Stopwatch


class _MetricSender(threading.Thread):
    """Thread responsible for sending metrics data."""

    def __init__(self):
        super().__init__()
        self.queue = Queue()
        self.daemon = True
        self.start()

    def run(self):
        while True:
            time.sleep(30)

            try:
                while True:  # Try read the queue until it fails
                    report_metric(*self.queue.get_nowait())
                    time.sleep(0.5)
            except Empty:
                pass  # If the queue is empty just continue the loop
            except Exception as e:
                LOG.error('Could not send Metrics: {}'.format(repr(e)))


_metric_uploader = _MetricSender()


def report_metric(name, data):
    """
    Report a general metric to the Mycroft servers

    Args:
        name (str): Name of metric. Must use only letters and hyphens
        data (dict): JSON dictionary to report. Must be valid JSON
    """
    try:
        if Configuration().get('opt_in', False):
            MetricsApi().report_metric(name, data)
    except Exception as e:
        LOG.error(f'Metric couldn\'t be uploaded, due to a network error ({e})')


def report_timing(ident, system, timing, additional_data=None):
    """Create standardized message for reporting timing.

    Args:
        ident (str):            identifier of user interaction
        system (str):           system the that's generated the report
        timing (stopwatch):     Stopwatch object with recorded timing
        additional_data (dict): dictionary with related data
    """
    additional_data = additional_data or {}
    report = copy(additional_data)
    report['id'] = ident
    report['system'] = system
    report['start_time'] = timing.timestamp
    report['time'] = timing.time

    _metric_uploader.queue.put(('timing', report))


class MetricsAggregator:
    """
    MetricsAggregator is not threadsafe, and multiple clients writing the
    same metric "concurrently" may result in data loss.
    """

    def __init__(self):
        self._counters = {}
        self._timers = {}
        self._levels = {}
        self._attributes = {}
        self.attr("version", OVOS_VERSION_STR)

    def increment(self, name, value=1):
        cur = self._counters.get(name, 0)
        self._counters[name] = cur + value

    def timer(self, name, value):
        cur = self._timers.get(name)
        if not cur:
            self._timers[name] = []
            cur = self._timers[name] = []
        cur.append(value)

    def level(self, name, value):
        self._levels[name] = value

    def clear(self):
        self._counters = {}
        self._timers = {}
        self._levels = {}
        self._attributes = {}
        self.attr("version", OVOS_VERSION_STR)

    def attr(self, name, value):
        self._attributes[name] = value

    def flush(self):
        publisher = MetricsPublisher()
        payload = {
            'counters': self._counters,
            'timers': self._timers,
            'levels': self._levels,
            'attributes': self._attributes
        }
        self.clear()
        count = (len(payload['counters']) + len(payload['timers']) +
                 len(payload['levels']))
        if count > 0:
            # LOG.debug(json.dumps(payload))

            def publish():
                publisher.publish(payload)

            threading.Thread(target=publish).start()


class MetricsPublisher:
    def __init__(self, url=None, enabled=False):
        conf = Configuration().get('server') or {}
        self.url = url or conf.get('url')
        self.enabled = enabled or conf.get('metrics', False)

    def publish(self, events):
        if 'session_id' not in events:
            session_id = SessionManager.get().session_id
            events['session_id'] = session_id
        if self.enabled:
            requests.post(
                self.url,
                headers={'Content-Type': 'application/json'},
                data=json.dumps(events), verify=False)
