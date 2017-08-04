# -*- coding: utf-8 -*-
from __future__ import division
from autobahn.twisted.wamp import ApplicationSession, ApplicationRunner
from twisted.internet.defer import inlineCallbacks
from autobahn.twisted.wamp import Application
from autobahn.twisted.util import sleep
import socket, requests, psutil

SERVER = u'127.0.0.1'
TOPIC = u'clientstats'

# We create the WAMP client.
app = Application('monitoring')
# First, we use a trick to know the public IP for this
# machine.
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.connect(("8.8.8.8", 80))
# We attach a dict to the app, so that its
# reference is accessible from anywhere.
app._params = {'name': socket.gethostname(), 'ip': s.getsockname()[0]}
s.close()


# We subscribe to the "clientconfig" WAMP event.
@app.subscribe(u'clientconfig.{}'.format(app._params['ip']))
def update_configuration(args):
    """ Update the client configuration when Django asks for it. """
    app._params.update(args)

def to_gib(bytes, factor=2 ** 30, suffix="GiB"):
    """ 
    Convert a number of bytes to Gibibytes
    Ex : 1073741824 bytes = 1073741824/2**30 = 1GiB
    """
    return "%0.2f%s" % (bytes / factor, suffix)

def get_stats(filters={}):
    """ 
    Returns the current values for CPU/memory/disk usage.

    These values are returned as a dict such as:

        {
            'cpus': ['x%', 'y%', etc],
            'memory': "z%",
            'disk':{
                '/partition/1': 'x/y (z%)',
                '/partition/2': 'x/y (z%)',
                etc
            }
        }

    The filter parameter is a dict such as:

        {'cpus': bool, 'memory':bool, 'disk':bool}

    It's used to decide to include or not values for the 3 types of
    ressources.
    """

    results = {}

    if (filters.get('show_cpus', True)):
        results['cpus'] = tuple("%s%%" % x for x in psutil.cpu_percent(percpu=True))

    if (filters.get('show_memory', True)):
        memory = psutil.virtual_memory()
        results['memory'] = '{used}/{total} ({percent}%)'.format(
            used=to_gib(memory.used),
            total=to_gib(memory.total),
            percent=memory.percent
        )

    if (filters.get('show_disk', True)):
        disks = {}
        for device in psutil.disk_partitions():
            # skip mountpoint not actually mounted (like CD drives with no disk on Windows)
            if device.fstype != "":
                usage = psutil.disk_usage(device.mountpoint)
                disks[device.mountpoint] = '{used}/{total} ({percent}%)'.format(
                    used=to_gib(usage.used),
                    total=to_gib(usage.total),
                    percent=usage.percent
                )

        results['disks'] = disks

        return results


class AppSession(ApplicationSession):

    @inlineCallbacks
    def onJoin(self, details):
        print ("Connected...")
        # self.log.info('session joined: {details}', details=details)
        response = requests.post('http://' + SERVER + ':8080/clients/', data={'ip': app._params['ip']})
        if response.status_code == 200:
            app._params.update(response.json())
        else:
            print("Could not retrieve configuration for client: {} ({})".format(response.reason, response.status_code))

        while True:
            print("Tick")
            try:
                 # Every time we loop, we get the stats for our machine
                stats = {'ip': app._params['ip'], 'name': app._params['name']}
                stats.update(get_stats(app._params))

                # If we are requested to send the stats, we publish them using WAMP.
                if not app._params['disabled']:
                    self.publish(TOPIC, stats)
                    print("Stats published: {}".format(stats))
                # Then we wait. Thanks to @inlineCallbacks, using yield means we
                # won't block here, so our client can still listen to WAMP events
                # and react to them.
                yield sleep(app._params['frequency'])
            except Exception as e:
                print("Error in stats loop: {}".format(e))
                break


if __name__ == '__main__':
    runner = ApplicationRunner(u'ws://{}:8080/ws'.format(SERVER), u'realm1')
    runner.run(AppSession)
