import math
import struct
import wave

import pytest

from _ma_playback import ffi, lib

from tinytag import TinyTagException

from just_playback.playback import Playback, MiniaudioError


def test_miniaudioerror_str_no_args():
    err = MiniaudioError()
    assert str(err) == "UNKNOWN MA_ERROR"


def test_miniaudioerror_str_with_string_arg():
    err = MiniaudioError("some message")
    assert str(err) == "some message"


def test_miniaudioerror_str_with_non_string_arg():
    err = MiniaudioError(123)
    assert str(err) == "123"


def _write_sine_wave(path, *, sample_rate=44100, duration=0.1, frequency=440):
    total_frames = int(sample_rate * duration)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        frames = bytearray()
        for n in range(total_frames):
            sample = int(32767 * math.sin(2 * math.pi * frequency * n / sample_rate))
            frames += struct.pack("<h", sample)
        wav_file.writeframes(frames)


def test_check_available_playback_devices_cleans_up_on_failure():
    attrs = ffi.new("Attrs *")
    lib.set_force_device_enumeration_failure(True)
    try:
        res = lib.check_available_playback_devices(attrs)
    finally:
        lib.set_force_device_enumeration_failure(False)

    assert res != 0
    assert lib.last_device_probe_cleaned_up()


def test_check_available_playback_devices_sets_device_count_and_cleans_up_on_success():
    attrs = ffi.new("Attrs *")

    res = lib.check_available_playback_devices(attrs)

    assert res == 0
    assert attrs.num_playback_devices >= 0
    if attrs.num_playback_devices == 0:
        pytest.skip("No playback devices available on this test system")

    assert attrs.num_playback_devices > 0
    assert lib.last_device_probe_cleaned_up()


def test_load_file_uses_decoder_duration_when_tinytag_fails(tmp_path, monkeypatch):
    audio_path = tmp_path / "tone.wav"
    _write_sine_wave(audio_path)

    playback = Playback()

    monkeypatch.setattr(
        "just_playback.playback.TinyTag.get",
        lambda _: (_ for _ in ()).throw(TinyTagException("tinytag boom")),
        raising=False,
    )

    playback.load_file(str(audio_path))

    assert playback.duration > 0

    playback.seek(playback.duration / 2)


def test_load_file_propagates_unexpected_tinytag_errors(tmp_path, monkeypatch):
    audio_path = tmp_path / "tone.wav"
    _write_sine_wave(audio_path)

    playback = Playback()

    monkeypatch.setattr(
        "just_playback.playback.TinyTag.get",
        lambda _: (_ for _ in ()).throw(ValueError("unexpected tinytag failure")),
        raising=False,
    )

    with pytest.raises(ValueError, match="unexpected tinytag failure"):
        playback.load_file(str(audio_path))


def test_failed_init_audio_stream_leaves_playback_unready(tmp_path):
    audio_path = tmp_path / "tone.wav"
    _write_sine_wave(audio_path)

    playback = Playback()

    lib.set_force_device_init_failure(True)
    try:
        with pytest.raises(MiniaudioError):
            playback.load_file(str(audio_path))
    finally:
        lib.set_force_device_init_failure(False)

    assert playback.curr_pos == -1
    assert playback.active is False
