"""Collect diamonds on an existing battle; never start, equip or end a run."""
import argparse
import time
import settings
from device import capture
from runtime import logger
from vision import wave_reader
from interactions.ad_gems import AdGemCollector
from flows.shard import GemOrbitTapper, gem_orbit_opts


def main(instance, preset):
    settings.select_instance(instance,preset)
    body=settings.CONFIG['presets'].get(preset) or {}
    gather=body.get('gather') or {}
    ads=AdGemCollector(gather.get('ad_gems',True))
    orbit=GemOrbitTapper(**gem_orbit_opts())
    logger.event('gem_collector_started',preset=preset,ad_gems=ads.enabled,orbit=orbit.enabled)
    last_status=None
    while True:
        frame=capture.grab()
        live=wave_reader.read_wave(frame) is not None
        state='collecting' if live else 'waiting_for_battle'
        if state!=last_status:
            logger.event('gem_collector_state',state=state)
            last_status=state
        if live:
            ads.poll(frame)
            # Ad claim may have changed the screen; refresh before orbit taps.
            orbit.poll(capture.grab())
        time.sleep(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance',required=True)
    parser.add_argument('--preset',required=True)
    args=parser.parse_args()
    try:main(args.instance,args.preset)
    except Exception as exc:
        logger.event('gem_collector_failed',error=str(exc))
        raise
