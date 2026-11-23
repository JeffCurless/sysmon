"""
Optional case-fan tachometer reader for sysmon, using an RPi.GPIO
falling-edge interrupt on a BCM pin.
"""
import time

from RPi import GPIO

WAIT_TIME = 1

class GetCaseFanSpeed:
    '''
    GPIO tachometer reader for a case fan.

    Counts falling-edge pulses on a BCM GPIO pin (pulled high) and converts
    the inter-pulse interval to RPM.  Assumes PULSE (2) pulses per revolution,
    which is typical for 3-pin PC-style fans.

    Parameters:
        tachPin - BCM GPIO pin number connected to the fan tachometer signal.
    '''
    TACH = 18
    PULSE = 2
    def __init__( self, tachPin: int = TACH) -> None:
        '''
        Initialise GPIO, configure the tachometer input pin with pull-up, and
        register the falling-edge interrupt that measures RPM.

        Parameters:
            tachPin - BCM GPIO pin number for the tachometer signal.
        '''
        self._tachPin = tachPin
        self._closed = False
        self._rpm = 0.0
        self._t   = time.time()
        self.GPIO = GPIO
        self.GPIO.setmode( self.GPIO.BCM )
        self.GPIO.setwarnings( False )
        self.GPIO.setup( self._tachPin, self.GPIO.IN, pull_up_down=self.GPIO.PUD_UP )
        self.GPIO.add_event_detect( self._tachPin, self.GPIO.FALLING, self._calcRPM )

    def close( self ) -> None:
        '''
        Stop the tachometer interrupt and release this object's GPIO pin.
        Call this explicitly on shutdown; it is safe to call more than once.
        Only the tachometer pin is cleaned up, not every channel in use.
        '''
        if self._closed:
            return
        self._closed = True
        self.GPIO.remove_event_detect( self._tachPin )
        self.GPIO.cleanup( self._tachPin )

    def __enter__( self ) -> "GetCaseFanSpeed":
        return self

    def __exit__( self, *_exc ) -> None:
        self.close()

    def _calcRPM( self, _channel: int ) -> None:
        '''
        GPIO interrupt callback — calculates RPM from the time between
        consecutive falling edges.  Pulses shorter than 5 ms are rejected as
        spurious bounces.

        Parameters:
            _channel - BCM pin number supplied by the GPIO interrupt framework
                       (unused; the instance already knows its pin).
        '''
        dt = time.time() - self._t
        if dt < 0.005:
            return # Reject spuriously short pulses

        freq = 1 / dt
        self._rpm = (freq / GetCaseFanSpeed.PULSE) * 60
        self._t = time.time()

    @property
    def RPM( self ) -> float:
        '''
        Most-recently computed fan speed in revolutions per minute.

        Returns:
            Fan speed as a floating-point RPM value; 0.0 until the first
            valid pulse pair has been received, or if no pulse has arrived
            within the last WAIT_TIME seconds (fan stopped).
        '''
        if time.time() - self._t > WAIT_TIME:
            return 0.0
        return self._rpm

if __name__ == "__main__":

    with GetCaseFanSpeed() as fanSpeed:
        try:
            for _ in range( 10 ):
                print( f"{fanSpeed.RPM:.0f} RPM" )
                time.sleep( 2 )
        except KeyboardInterrupt:
            pass
