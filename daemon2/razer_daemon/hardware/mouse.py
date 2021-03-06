"""
Mouse class
"""
from razer_daemon.hardware.device_base import RazerDeviceBrightnessSuspend

class RazerMambaChroma(RazerDeviceBrightnessSuspend):
    """
    Class for the BlackWidow Chroma
    """
    USB_VID = 0x1532
    USB_PID = 0x0045

    METHODS = ['get_firmware', 'get_device_type', 'get_brightness', 'set_brightness', 'get_battery', 'is_charging', 'set_wave_effect',
               'set_static_effect', 'set_spectrum_effect', 'set_reactive_effect', 'set_none_effect', 'set_breath_random_effect',
               'set_breath_single_effect', 'set_breath_dual_effect', 'set_custom_effect', 'set_key_row',
               'set_charge_effect', 'set_charge_colour', 'set_idle_time', 'set_low_battery_threshold', 'set_dpi_xy']
