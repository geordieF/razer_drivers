"""
Daemon class

This class is the main core of the daemon, this serves a basic dbus module to control the main bit of the daemon
"""
import configparser
import logging
import logging.handlers
import os
import sys
import signal
import time
import tempfile

import dbus.mainloop.glib
from gi.repository import GObject

import razer_daemon.hardware
from razer_daemon.dbus_services.service import DBusService
from razer_daemon.device import DeviceCollection
from razer_daemon.misc.screensaver_thread import ScreensaverThread

DEVICE_CHECK_INTERVAL = 5000 # Milliseconds

def daemonize(foreground=False, verbose=False, log_dir=None, console_log=False, run_dir=None, config_file=None, pid_file=None):
    """
    Performs double fork behaviour of daemons

    :param foreground: Run in foreground (don't fork)
    :type foreground: bool

    :param verbose: Verbose mode
    :type verbose: bool

    :param log_dir: Log directory
    :type log_dir: str

    :param console_log: Log to console
    :type console_log: bool

    :param run_dir: Run/Home directory
    :type run_dir: str

    :param config_file: Config filepath
    :type config_file: str

    :param pid_file: PID filepath (wont create a file if None)
    :type pid_file: str or None
    """

    if not foreground:
        # Attempt to double fork
        try:
            pid = os.fork()
            # Returns 0 in the child process, and returns the child's pid in the parent so
            # by checking if greater than 0 we can close parent
            if pid > 0:
                sys.exit(0)
        except OSError as err:
            print("Failed first fork. Error: {0}".format(err))

        # Become the process group and session leader
        os.setsid()

        try:
            pid = os.fork()
            # Returns 0 in the child process, and returns the child's pid in the parent so
            # by checking if greater than 0 we can close parent
            if pid > 0:
                sys.exit(0)
        except OSError as err:
            print("Failed first fork. Error: {0}".format(err))

        # Set umask to 0
        os.umask(0)

        # Close stdin, stdout, stderr
        sys.stdout.flush()
        sys.stderr.flush()
        stdin = open('/dev/null', 'r')
        stdout = open('/dev/null', 'a+')
        os.dup2(stdin.fileno(), sys.stdin.fileno())
        os.dup2(stdout.fileno(), sys.stdout.fileno())
        os.dup2(stdout.fileno(), sys.stderr.fileno())


    # Change working directory
    if run_dir is not None and os.path.exists(run_dir) and os.path.isdir(run_dir):
        os.chdir(run_dir)
    else:
        run_dir = tempfile.mkdtemp(prefix='tmp_', suffix='_razer_daemon')
        os.chdir(run_dir)

    # Write PID
    if pid_file is not None:
        try:
            with open(pid_file, 'w') as pid_file_obj:
                pid_file_obj.write(str(os.getpid()))
        except (OSError, IOError) as err:
            print("Error: {0}".format(err))

    # Create daemon and run
    daemon = RazerDaemon(verbose, log_dir, console_log, run_dir, config_file)
    try:
        daemon.run()
    except Exception as err:
        daemon.logger.exception("Caught exception", exc_info=err)

    # If pid file exists, remove it
    if pid_file is not None and os.path.exists(pid_file):
        os.remove(pid_file)


class RazerDaemon(DBusService):
    """
    Daemon class

    This class sets up the main run loop which serves DBus messages. The logger is initialised
    in this module as well as finding and initialising devices.

    Serves the following functions via DBus
    * getDevices - Returns a list of serial numbers
    * enableTurnOffOnScreensaver - Starts/Continues the run loop on the screensaver thread
    * disableTurnOffOnScreensaver - Pauses the run loop on the screensaver thread
    """

    BUS_PATH = 'org.razer'

    def __init__(self, verbose=False, log_dir=None, console_log=False, run_dir=None, config_file=None):
        self._data_dir = run_dir
        self._config_file = config_file
        self._config = configparser.ConfigParser()
        self.read_config(config_file)

        # Setup DBus to use gobject main loop
        dbus.mainloop.glib.threads_init()
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        DBusService.__init__(self, self.BUS_PATH, '/org/razer')

        self._main_loop = GObject.MainLoop()

        # Logging
        logging_level = logging.INFO
        if verbose or self._config.getboolean('General', 'verbose_logging'):
            logging_level = logging.DEBUG

        self.logger = logging.getLogger('razer')
        self.logger.setLevel(logging_level)
        formatter = logging.Formatter('%(asctime)s | %(name)-30s | %(levelname)-8s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        # Dont propagate to default logger
        self.logger.propagate = 0

        if console_log:
            console_logger = logging.StreamHandler()
            console_logger.setLevel(logging_level)
            console_logger.setFormatter(formatter)
            self.logger.addHandler(console_logger)

        if log_dir is not None:
            log_file = os.path.join(log_dir, 'razer.log')
            file_logger = logging.handlers.RotatingFileHandler(log_file, maxBytes=16777216, backupCount=10) # 16MiB
            file_logger.setLevel(logging_level)
            file_logger.setFormatter(formatter)
            self.logger.addHandler(file_logger)

        self.logger.info("Initialising Daemon. Pid: %d", os.getpid())

        # Setup screensaver thread
        self._screensaver_thread = ScreensaverThread(self, active=self._config.getboolean('Startup', 'devices_off_on_screensaver'))
        self._screensaver_thread.start()

        self._razer_devices = DeviceCollection()
        self._load_devices()

        # Add DBus methods
        self.logger.info("Adding razer.devices.getDevices method to DBus")
        self.add_dbus_method('razer.devices', 'getDevices', self.get_serial_list, out_signature='as')
        self.logger.info("Adding razer.devices.enableTurnOffOnScreensaver method to DBus")
        self.add_dbus_method('razer.devices', 'enableTurnOffOnScreensaver', self.enable_turn_off_on_screensaver)
        self.logger.info("Adding razer.devices.disableTurnOffOnScreensaver method to DBus")
        self.add_dbus_method('razer.devices', 'disableTurnOffOnScreensaver', self.disable_turn_off_on_screensaver)
        self.logger.info("Adding razer.devices.syncEffects method to DBus")
        self.add_dbus_method('razer.devices', 'syncEffects', self.sync_effects, in_signature='b')
        self.logger.info("Adding razer.daemon.stop method to DBus")
        self.add_dbus_method('razer.daemon', 'stop', self.stop)

        # TODO remove
        self.sync_effects(self._config.getboolean('Startup', 'sync_effects_enabled'))
        # TODO ======

        # Setup quit signals
        signal.signal(signal.SIGINT, self.quit)
        signal.signal(signal.SIGTERM, self.quit)

    def read_config(self, config_file):
        """
        Read in the config file and set the defaults

        :param config_file: Config file
        :type config_file: str or None
        """
        if config_file is not None and os.path.exists(config_file):
            self._config.read(config_file)

        self._config['DEFAULT'] = {
            'verbose_logging': True,
            'sync_effects_enabled': True,
            'devices_off_on_screensaver': True,
            'key_statistics': False,
        }

    def enable_turn_off_on_screensaver(self):
        """
        Enable the turning off of devices when the screensaver is active
        """
        self._screensaver_thread.active = True

    def disable_turn_off_on_screensaver(self):
        """
        Disable the turning off of devices when the screensaver is active
        """
        self._screensaver_thread.active = False

    def suspend_devices(self):
        """
        Suspend all devices
        """
        for device in self._razer_devices:
            device.dbus.suspend_device()

    def resume_devices(self):
        """
        Resume all devices
        """
        for device in self._razer_devices:
            device.dbus.resume_device()

    def get_serial_list(self):
        """
        Get list of devices serials
        """
        serial_list = self._razer_devices.serials()
        self.logger.debug('DBus called get_serial_list')
        return serial_list

    def sync_effects(self, enabled):
        """
        Sync the effects across the devices

        :param enabled: True to sync effects
        :type enabled: bool
        """
        # Todo perhaps move logic to device collection
        for device in self._razer_devices.devices:
            device.dbus.effect_sync = enabled

    def _load_devices(self):
        """
        Go through supported devices and load them

        Loops through the available hardware classes, loops through
        each device in the system and adds it if needs be.
        """
        devices = os.listdir('/sys/bus/hid/devices')
        classes = razer_daemon.hardware.get_device_classes()

        device_number = 0
        for device_class in classes:
            for device_id in devices:
                if device_id in self._razer_devices:
                    continue

                if device_class.match(device_id):
                    self.logger.info('Found device.%d: %s', device_number, device_id)
                    device_path = os.path.join('/sys/bus/hid/devices', device_id)
                    razer_device = device_class(device_path, device_number, self._config)
                    device_serial = razer_device.get_serial()
                    self._razer_devices.add(device_id, device_serial, razer_device)

                    device_number += 1

    def _remove_devices(self):
        """
        Go through the list of current devices and if they no longer exist then remove them
        """
        devices_to_remove = []

        for device in self._razer_devices:
            device_path = os.path.join('/sys/bus/hid/devices', device.device_id)
            if not os.path.exists(device_path):
                # Remove from DBus
                device.dbus.remove_from_connection()
                devices_to_remove.append(device.device_id)

        for device_id in devices_to_remove:
            # Remove device
            self.logger.warning("Device %s is missing. Removing from DBus", device_id)
            del self._razer_devices[device_id]

    def run(self):
        """
        Run the daemon
        """
        self.logger.info('Serving DBus')

        # Counter for managing periodic tasks
        counter = 0

        # Can't just use mainloop.run() as that blocks and
        # then signaling exit doesn't work
        main_loop_context = self._main_loop.get_context()
        while self._main_loop is not None:
            if main_loop_context.pending():
                main_loop_context.iteration()
            else:
                time.sleep(0.001)

            if counter > DEVICE_CHECK_INTERVAL: # Time sleeps 1ms so DEVICE_CHECK_INTERVAL is in milliseconds
                self._remove_devices()
                self._load_devices()
                counter = 0
            counter += 1

    def stop(self):
        """
        Wrapper for quit
        """
        self.quit(None, None)

    def quit(self, signum, frame):
        """
        Quit by stopping the main loop and screensaver thread
        """
        # pylint: disable=unused-argument
        self.logger.info('Stopping daemon.')
        self._main_loop = None

        # Stop screensaver
        self._screensaver_thread.shutdown = True
        self._screensaver_thread.join(timeout=2)
        if self._screensaver_thread.is_alive():
            self.logger.warning('Could not stop the screensaver thread')

        for device in self._razer_devices:
            device.dbus.close()


if __name__ == '__main__':
    # pylint: disable=invalid-name
    daemon_obj = RazerDaemon(verbose=True, console_log=True, log_dir='/tmp/razer_daemon/logs', run_dir='/tmp/razer_daemon/home')

    try:
        daemon_obj.run()
    except Exception as daemon_err:
        daemon_obj.logger.exception("Caught exception", exc_info=daemon_err)



