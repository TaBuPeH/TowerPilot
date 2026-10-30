"""The scenarios of the orchestrator golden answers: the adoption path of a battle already on screen (T3-0).

Each function runs `orchestrator.main()` once in a LoopWorld and returns its observation (the journal of device calls and ledger writes,
the ledger as left, how the loop ended). The recorded documents are backend/tests/fixtures/takeover_golden/orchestrator_adoption.json
and fleet_marks.json."""
from flows import shard
from interactions import tourney

from takeover_golden_loop import LoopWorld, frame


def world(tmp_path, monkeypatch, table, **options):
    return LoopWorld(tmp_path, monkeypatch, frames=list(table), table=table, **options)


# ---------------------------------------------------------------- start of the process
def battle_on_screen(tmp_path, monkeypatch):
    """The runner starts over a live battle: origin adopted when a wave reads; _prepare_coin_start returns because the screen is not Home."""
    table = {'initial': frame(5000), 'coin_check': frame(5000), 'live': frame(5001, status='refresh')}
    return world(tmp_path, monkeypatch, table).run()


def battle_on_screen_without_a_ledger_operation(tmp_path, monkeypatch):
    """The same start with no accepted operation (a hand-started runner): no session, no ledger."""
    table = {'initial': frame(5000), 'coin_check': frame(5000), 'live': frame(5001, status='refresh')}
    return world(tmp_path, monkeypatch, table, session=False).run()


def home_start(tmp_path, monkeypatch):
    """The runner starts on Home: origin started; the loadout, the tier and the entry run in order, then the loop observes the new battle."""
    table = {'initial': frame(None, screen_name='home'), 'coin_check': frame(None, screen_name='home'),
             'entered': frame(1, status='refresh')}
    return world(tmp_path, monkeypatch, table, preset_extra=dict(loadout='golden_loadout')).run()


def home_start_refused_entry(tmp_path, monkeypatch):
    """Entry on Home raises a refusal: coin_start_prepared ok=False with a shot, and the loop is left to observe (a person's battle will be adopted)."""
    table = {'initial': frame(None, screen_name='home'), 'coin_check': frame(None, screen_name='home'),
             'refused_shot': frame(None, screen_name='home')}
    run = world(tmp_path, monkeypatch, table)

    def refuse(body):
        raise tourney.Abort('synthetic entry refusal')
    monkeypatch.setattr(shard, 'enter_run', refuse)
    return run.run()


def tournament_preset_over_a_live_battle(tmp_path, monkeypatch):
    """A tournament run starts over a live battle: its one-shot setup is skipped (tourney_setup_skipped), the loop guards the run that exists."""
    table = {'initial': frame(8000), 'setup_check': frame(8000), 'live': frame(8001, status='refresh')}
    return world(tmp_path, monkeypatch, table, preset_extra=dict(kind='tournament', tournament_setup=True)).run()


def tournament_preset_on_home(tmp_path, monkeypatch):
    """A tournament run starts with no battle: the one-shot setup runs once with the durable setup callback."""
    table = {'initial': frame(None, screen_name='home'), 'setup_check': frame(None, screen_name='home'),
             'live': frame(1, status='refresh')}
    return world(tmp_path, monkeypatch, table, preset_extra=dict(kind='tournament', tournament_setup=True)).run()


# ---------------------------------------------------------------- the death branch
DEATH = frame(None, screen_name='game_stats', status='dead', stats=True)


def death_frames(**overrides):
    """A live battle, then the death dialog (GAME STATS matched), the frame after stats collection, and the fresh dialog the RETRY tap is read from."""
    return {'initial': frame(5000), 'coin_check': frame(5000), 'live': frame(5000, status='refresh'),
            'death': dict(DEATH, **overrides.get('death', {})), 'after_stats': frame(None, screen_name='game_stats'),
            'fresh_death': frame(None, screen_name='game_stats', retry=(540, 1500), status='refresh'), **overrides.get('extra', {})}


def death_plain(tmp_path, monkeypatch):
    """Ordinary death with the loop's defaults: stats collected, no rule, no pending stop, repeat on, a restart_via_home run restarting from Home."""
    return world(tmp_path, monkeypatch, death_frames(), preset_extra=dict(restart_via_home=False)).run()


def death_restart_via_home(tmp_path, monkeypatch):
    return world(tmp_path, monkeypatch, death_frames(), preset_extra=dict(restart_via_home=True), restart_result=True).run()


def death_restart_via_home_fails_then_retry(tmp_path, monkeypatch):
    return world(tmp_path, monkeypatch, death_frames(), preset_extra=dict(restart_via_home=True), restart_result=False).run()


def death_with_pending_stop_after_run(tmp_path, monkeypatch):
    """The scheduler's boundary request: the loop leaves at its death handler, after stats and the rules, before any restart. The flag stays."""
    return world(tmp_path, monkeypatch, death_frames(), preset_extra=dict(restart_via_home=False), stop_flag='scheduler transition').run()


def death_with_repeat_off(tmp_path, monkeypatch):
    """Live repeat switched off: stop_after_run (live_repeat_disabled) and the loop returns; the run flag file is never read as a stop."""
    return world(tmp_path, monkeypatch, death_frames(), repeat=False).run()


def death_without_game_stats(tmp_path, monkeypatch):
    """The dialog does not match GAME STATS: no stats collection, the rest of the branch is unchanged."""
    table = death_frames(death=dict(stats=False), extra={'after_stats': frame(None, screen_name='game_stats', retry=(540, 1500))})
    return world(tmp_path, monkeypatch, table, preset_extra=dict(restart_via_home=False)).run()


def death_with_stats_artifact(tmp_path, monkeypatch):
    """Stats collection returns an artifact: its capture status is written into the battle's runtime."""
    return world(tmp_path, monkeypatch, death_frames(), preset_extra=dict(restart_via_home=False), stats_artifact=True).run()


def death_rules_navigate(tmp_path, monkeypatch):
    """A death-phase rule touched the screen: the frame is grabbed again before the restart decision."""
    table = {'initial': frame(5000), 'coin_check': frame(5000), 'live': frame(5000, status='refresh'), 'death': DEATH,
             'after_stats': frame(None, screen_name='game_stats'), 'after_rules': frame(None, screen_name='game_stats'),
             'fresh_death': frame(None, screen_name='game_stats', retry=(540, 1500), status='refresh')}
    return world(tmp_path, monkeypatch, table, preset_extra=dict(restart_via_home=False), death_rules=True).run()


def death_before_the_entry_was_observed(tmp_path, monkeypatch):
    """The first frame the loop judges is a death dialog (the battle never read): the prepared record is interrupted and the process returns, no stats, no restart."""
    table = {'initial': frame(None, screen_name='game_stats'), 'coin_check': frame(None, screen_name='game_stats'),
             'death': DEATH}
    return world(tmp_path, monkeypatch, table).run()


def death_without_a_ledger_operation(tmp_path, monkeypatch):
    """No accepted operation: the same branch with no session (stats collection without a battle, no finish, no prepare_next)."""
    return world(tmp_path, monkeypatch, death_frames(), session=False, preset_extra=dict(restart_via_home=False)).run()


def death_retry_button_gone(tmp_path, monkeypatch):
    """The dialog is no longer on screen when the RETRY tap is judged: retry_skipped and a shot, no tap."""
    table = death_frames(extra={'fresh_death': frame(None, screen_name='game_stats', status='refresh')})
    return world(tmp_path, monkeypatch, table, preset_extra=dict(restart_via_home=False)).run()


def death_retry_tap_refused(tmp_path, monkeypatch):
    return world(tmp_path, monkeypatch, death_frames(), preset_extra=dict(restart_via_home=False), tap_refused=True).run()


# ---------------------------------------------------------------- a replaced battle
def waves(*reads, final=None):
    """A live battle frame per wave read, then (optionally) one more named `final`."""
    table = {'initial': frame(reads[0]), 'coin_check': frame(reads[0])}
    for number, wave in enumerate(reads[1:], 1):
        table[f'w{number}'] = frame(wave, status='refresh')
    return table


def replaced_battle_confirmed(tmp_path, monkeypatch):
    """5000, then three consistent low reads: the tracker confirms the drop, the old record is finished wave_went_backwards, prepare_next
    names it as predecessor (partial_adoption), a fresh RunState reads on."""
    table = waves(5000, 5000, 100, 101, 102, 103)
    return world(tmp_path, monkeypatch, table).run()


def replaced_battle_without_a_ledger_operation(tmp_path, monkeypatch):
    table = waves(5000, 5000, 100, 101, 102, 103)
    return world(tmp_path, monkeypatch, table, session=False).run()


def one_big_dip_stays_on_probation(tmp_path, monkeypatch):
    """A single implausible low read (5000 then 100 then 5001): held (no watch, no record change), then the run continues."""
    table = waves(5000, 5000, 100, 5001, 5002)
    return world(tmp_path, monkeypatch, table).run()


def two_big_dips_stay_on_probation(tmp_path, monkeypatch):
    """Two consistent low reads are one short of the three the tracker needs: still the same run."""
    table = waves(5000, 5000, 100, 101, 5003)
    return world(tmp_path, monkeypatch, table).run()


def small_dip_below_the_margin(tmp_path, monkeypatch):
    """A dip under the 100-wave margin never reaches the boundary check; the tracker's probation alone holds it."""
    table = waves(5000, 5000, 4950, 4960, 5004)
    return world(tmp_path, monkeypatch, table).run()


ADOPTION = {fn.__name__: fn for fn in (
    battle_on_screen, battle_on_screen_without_a_ledger_operation, home_start, home_start_refused_entry,
    tournament_preset_over_a_live_battle, tournament_preset_on_home,
    death_plain, death_restart_via_home, death_restart_via_home_fails_then_retry, death_with_pending_stop_after_run, death_with_repeat_off,
    death_without_game_stats, death_with_stats_artifact, death_rules_navigate, death_before_the_entry_was_observed,
    death_without_a_ledger_operation, death_retry_button_gone, death_retry_tap_refused,
    replaced_battle_confirmed, replaced_battle_without_a_ledger_operation, one_big_dip_stays_on_probation,
    two_big_dips_stay_on_probation, small_dip_below_the_margin)}
