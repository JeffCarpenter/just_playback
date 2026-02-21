import pathlib
import math
import platform
import logging
from typing import Optional

from tinytag import TinyTag, TinyTagException
from _ma_playback import ffi, lib
from .ma_result import MA_RESULT_STR, MiniaudioError

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


class Playback:
    """
        A class for controlling playback of a single audio file in a way that's independent of the audio
        file's format. Works for all file formats supported by https://github.com/mackron/miniaudio.
    """
    
    def __init__(self, path_to_file: Optional[str] = ''):
        self.__ma_attrs = ffi.new("Attrs *")
        
        self.__bind(lib.check_available_playback_devices(self.__ma_attrs))

        if self.__ma_attrs.num_playback_devices < 1:
            raise MiniaudioError('No playback device is available!!')
        
        else:
            lib.init_attrs(self.__ma_attrs)
            
            self.__paused: bool = False
            self.__file_duration: Optional[float] = None

            if path_to_file:
                self.load_file(path_to_file)   
    
    def load_file(self, path_to_file: str) -> None:
        """
            Loads an audio file using one of the available backends.
            This also kills any active playback.

            Duration metadata is read from TinyTag first. If TinyTag fails or
            returns no duration, a decoder-based fallback is used.

        Args:
            path_to_file: The absolute or relative path to the audio file

        Throws a FileNotFoundError if the audio file is not found
        """

        audio_file = pathlib.Path(path_to_file)
        if not audio_file.exists() or not path_to_file:
            raise FileNotFoundError('Audio file not found: {}'.format(path_to_file))
        
        self.__bind(lib.terminate_audio_stream(self.__ma_attrs))
        
        if platform.system() == 'Windows':
            self.__bind(lib.load_file_w(self.__ma_attrs, path_to_file))
        else:
            self.__bind(lib.load_file(self.__ma_attrs, path_to_file.encode('utf-8')))

        self.__bind(lib.init_audio_stream(self.__ma_attrs))
        self.__bind(lib.set_device_volume(self.__ma_attrs))

        self.__file_duration = self.__determine_duration(path_to_file)

    def play(self) -> None:
        """
            Plays the loaded file from the beginning. Ignores the fact
            that the current playback might be paused. Has no effect if no 
            audio file has been loaded
        """

        if not self.__ma_attrs.audio_stream_ready:
            logging.error('No audio file has been loaded yet!!')
        
        else:
            if self.active:
                self.stop()

            else:
                if self.__ma_attrs.audio_stream_ended_naturally:
                    # the audio file played to completion meanwhile the audio device
                    # is still running so we've got to stop it.

                    self.__bind(lib.stop_audio_stream(self.__ma_attrs))
                    self.__ma_attrs.audio_stream_ended_naturally = False

            self.__ma_attrs.frame_offset = 0
            self.__ma_attrs.frame_offset_modified = True
            self.__paused = False
            self.__bind(lib.start_audio_stream(self.__ma_attrs))

    def stop(self) -> None:
        """
            Stops audio playback regardless of whether or not it is paused. 
            Has no effect if playback is inactive
        """

        if self.active:
            if not self.__paused:
                # only stop the audio stream if self.pause() didn't
                self.__bind(lib.stop_audio_stream(self.__ma_attrs))

            self.__ma_attrs.frame_offset = 0
            self.__paused = False
            
    def pause(self) -> None:
        """
            Pauses audio playback. Has no effect if playback is inactive or is
            already paused
        """

        if self.active and not self.__paused:
            self.__bind(lib.stop_audio_stream(self.__ma_attrs))
            self.__paused = True

    def resume(self) -> None:
        """ 
            Resumes audio playback. Has no effect if playback is inactive or is 
            not paused
        """

        if self.active and self.__paused:
            self.__paused = False
            self.__bind(lib.start_audio_stream(self.__ma_attrs))
    
    def seek(self, pos: float) -> None:
        """
            Moves playback to a specified position. Has no effect if playback is
            inactive
        
        Args:
            pos: position in seconds for playback to jump to. The value is always
                 clamped to >= 0 and is additionally clamped to <= self.duration
                 whenever duration metadata is available.
        """

        if self.active:
            pos = max(pos, 0)
            if self.__file_duration is not None:
                pos = min(pos, self.__file_duration)
            self.__ma_attrs.frame_offset = math.floor(pos * self.__ma_attrs.decoder.outputSampleRate)
            self.__ma_attrs.frame_offset_modified = True
    
    def set_volume(self, volume: float) -> None:
        """
            Sets the volume for the playback device. Persists
            across audio file loads until reset

        Args:
            volume: A value in the interval [0, 1].
        """

        self.__ma_attrs.playback_volume = min(max(volume, 0), 1)
        if self.active:
            self.__bind(lib.set_device_volume(self.__ma_attrs))
    
    def loop_at_end(self, loops_at_end: bool) -> None:
        """
            Set playback to automatically restart after playing to completion. 
            By default this doesn't happen. Persists across audio file loads until reset

        Args:
            loops_at_end: True if playback should loop, False otherwise
        """

        self.__ma_attrs.loops_at_end = loops_at_end
    
    @property
    def active(self) -> bool:
        """
            True if playback is playing or is paused and False otherwise
        """
    
        if not self.__ma_attrs.audio_stream_ready:
            return False
        
        else:
            return self.__ma_attrs.audio_stream_active or self.__paused
    
    @property
    def playing(self) -> bool:
        """
            True if playback is playing
        """

        if not self.__ma_attrs.audio_stream_ready:
            return False
        
        else:
            return self.__ma_attrs.audio_stream_active

    @property
    def curr_pos(self) -> float:
        """
            The playback's current position/offset in seconds from the
            beginning of audio file if playback is active or the file has 
            been loaded and -1 otherwise
        """

        if self.active:
            return self.__ma_attrs.frame_offset / self.__ma_attrs.decoder.outputSampleRate
        
        elif self.__ma_attrs.audio_stream_ready:
            return 0

        else:        
            return -1
    
    @property
    def paused(self) -> bool:
        return self.__paused
    
    @property
    def duration(self) -> float:
        """
            The length in seconds of the loaded audio file.
            Returns 0 if the file has not been loaded or the duration could not
            be determined.
        """

        return self.__file_duration if self.__file_duration is not None else 0.0
    
    @property
    def volume(self) -> float:
        """
            The playback's current volume, a value in the interval [0, 1]
        """

        if self.active:
            self.__bind(lib.get_device_volume(self.__ma_attrs))
        
        return self.__ma_attrs.playback_volume
    
    @property
    def loops_at_end(self) -> bool:
        return self.__ma_attrs.loops_at_end

    def __determine_duration(self, path_to_file: str) -> Optional[float]:
        """Return the file duration in seconds when available.

        TinyTag is queried first. If TinyTag fails or cannot determine a
        duration, this falls back to miniaudio decoder frame metadata.
        """
        try:
            duration = TinyTag.get(path_to_file).duration
        except TinyTagException as exc:
            logging.warning('TinyTag failed to read duration for %s: %s', path_to_file, exc)
            duration = None

        if duration is None:
            frame_count = ffi.new('ma_uint64 *')
            ma_res = lib.get_decoder_length_in_pcm_frames(self.__ma_attrs, frame_count)
            if ma_res == 0 and self.__ma_attrs.decoder.outputSampleRate:
                duration = frame_count[0] / self.__ma_attrs.decoder.outputSampleRate
            else:
                logging.warning('Unable to determine decoder duration for %s', path_to_file)

        return duration

    def __bind(self, ma_res: int) -> None:
        """ 
            Internal method for checking and throwing possible miniaudio errors. 
            DO NOT CALL
        """

        if ma_res:
            raise MiniaudioError(MA_RESULT_STR[ma_res])

    def __del__(self):
        self.__bind(lib.terminate_audio_stream(self.__ma_attrs))
