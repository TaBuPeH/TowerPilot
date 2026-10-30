"""A remembered store coordinate never authorizes spending guild coins.

All pixels are synthetic. The real scanner, identity matching, scrolling,
selection, purchase flow and FlowGuard run; hardware/process I/O is blocked.
"""
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from interactions import missions, store


def matches(frame, template):
    scores = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(scores >= .99)
    return [(int(x + template.shape[1]//2), int(y + template.shape[0]//2))
            for y, x in zip(ys, xs)]


@pytest.fixture
def world(monkeypatch, tmp_path):
    import socket
    import subprocess
    import settings
    from device import act, adbclient, capture

    def forbidden(*args, **kwargs):
        raise AssertionError('store regression attempted external I/O')

    for module, name in ((socket, 'socket'), (subprocess, 'Popen'),
                         (settings, 'run_hidden'), (adbclient, 'shell'),
                         (adbclient, 'exec_out'), (capture, 'grab')):
        monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(settings, 'template_path', lambda rel: tmp_path / rel)
    rng = np.random.default_rng(19)
    templates = {rel: rng.integers(0, 180, (8, 16, 3), dtype=np.uint8)
                 for rel in [*store._HEADERS.values(), *store._TITLES.values(),
                             'icons/guild_coin.png', 'buttons/store_owned.png']}
    monkeypatch.setattr(store.detect, '_tpl', lambda rel: templates[rel])
    monkeypatch.setattr(store, '_content_band', lambda frame: (120, 920))
    names = {1: 'battle_menu', 2: 'guild', 3: 'guild_store', 4: 'reward', 5: 'battle', 6: 'cards'}
    codes = {v: k for k, v in names.items()}
    state = SimpleNamespace(kind='battle_menu', offset=0, balance=150, inputs=[], events=[],
                            mode='success', purchased=False, catalogue_finished=False,
                            on_relocate=None, mutate=None, next_visit=False)
    # Every store section is present. Higher-priority cards other than the
    # chosen relic are owned; two chips remain distinct concrete alternatives.
    state.cards = [
        dict(category='gems', x=12, y=100, h=240, status='owned', price=50, seed=1),
        dict(category='background', x=12, y=480, h=250, status='owned', price=50, seed=2),
        dict(category='epic_relic', x=12, y=980, h=280, status='price', price=50, seed=3),
        dict(category='chip', x=12, y=1510, h=280, status='price', price=50, seed=4),
        dict(category='chip', x=254, y=1510, h=280, status='price', price=50, seed=5),
    ]
    state.target = state.cards[2]
    background = rng.integers(0, 45, (1900, 480, 3), dtype=np.uint8)
    header_positions = {'currencies': 30, 'cosmetics': 400, 'relics': 900, 'tower_guardian': 1430}

    def put(image, template, cx, cy):
        h, w = template.shape[:2]
        image[cy-h//2:cy-h//2+h, cx-w//2:cx-w//2+w] = template

    def content():
        image = background.copy()
        for category, y in header_positions.items():
            put(image, templates[store._HEADERS[category]], 240, y)
        for card in state.cards:
            x, y, h = card['x'], card['y'], card['h']
            image[y:y+h+1, x:x+211] = (35, 30, 60)
            cv2.rectangle(image, (x, y), (x+210, y+h), (245, 245, 245), 4)
            art = np.random.default_rng(card['seed']).integers(0, 180, (100, 160, 3), dtype=np.uint8)
            if card['category'] == 'epic_relic':
                # The established relic classifier compares magenta and cyan.
                art[:35] = (160, 0, 160)
            if card.get('blank'):
                art[:] = (35, 30, 60)
            image[y+55:y+155, x+25:x+185] = art
            if card['category'] in store._TITLES:
                put(image, templates[store._TITLES[card['category']]], x+105, y+20)
            cx, cy = x+105, y+h-30
            if card['status'] in {'price', 'owned'}:
                rel = 'icons/guild_coin.png' if card['status'] == 'price' else 'buttons/store_owned.png'
                put(image, templates[rel], cx, cy)
                image[cy-8, cx, 2] = card['price']
        return image

    def render():
        image = np.zeros((1000, 480, 3), np.uint8)
        image[0, 0, 0] = codes[state.kind]
        image[0, 1, 0] = 255 if state.balance is None else state.balance
        if state.kind == 'guild_store':
            image[120:920] = content()[state.offset:state.offset+800]
        return image

    def screen(frame):
        return names[int(frame[0, 0, 0])]

    def match(frame, rel, threshold):
        if rel == 'icons/guild_header.png':
            hit = screen(frame) in {'guild', 'guild_store'}
            return hit, 1.0 if hit else 0.0, (0, 0)
        template = templates[rel]
        scores = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(scores)
        return score >= threshold, score, loc

    def price_buttons(frame):
        return [(x, y, float(frame[y-8, x, 2]), 1.0)
                for x, y in matches(frame, templates['icons/guild_coin.png'])]

    monkeypatch.setattr(store, '_price_buttons', price_buttons)
    monkeypatch.setattr(store, '_owned_spots', lambda frame: matches(frame, templates['buttons/store_owned.png']))
    monkeypatch.setattr(store.detect, '_match', match)
    monkeypatch.setattr(store, 'reward_screen', screen)
    monkeypatch.setattr(missions, 'reward_screen', screen)
    monkeypatch.setattr(missions, 'return_target', lambda frame: (200, 970))
    monkeypatch.setattr(store, 'find_tile', lambda frame, rel: (100, 100))
    monkeypatch.setattr(missions, 'find_skip', lambda frame: (300, 100) if screen(frame) == 'reward' else None)
    monkeypatch.setattr(store, 'read_balance', lambda frame:
                        None if frame[0, 1, 0] == 255 else float(frame[0, 1, 0]))
    monkeypatch.setattr(store.logger, 'event', lambda kind, **kw: state.events.append((kind, kw)))
    monkeypatch.setattr(store.logger, 'shot', lambda *a: 'synthetic-shot')

    def tap(x, y, reason='', **kwargs):
        state.inputs.append((state.kind, state.offset, x, y, reason))
        if reason == 'guild_open': state.kind = 'guild'
        elif reason == 'store_tab': state.kind = 'guild_store'
        elif reason == 'return_to_game': state.kind = 'battle'
        elif reason == 'reward_skip' and state.mode != 'reward_timeout': state.kind = 'guild_store'
        elif reason.startswith('store_buy_'):
            assert not state.purchased, 'a single flow retried its purchase'
            state.purchased = True
            candidates = [c for c in state.cards if abs(c['x']+105-x) < 5
                          and abs(c['y']+c['h']-30-state.offset+120-y) < 5]
            assert len(candidates) == 1, 'tap did not bind to a current concrete card'
            state.bought = candidates[0]
            if state.mode == 'transport': raise OSError('ambiguous synthetic transport failure')
            if state.mode == 'refused': raise act.TapRefused('synthetic input refusal')
            if state.mode == 'dry': return {'dry_run': True}
            if state.mode != 'swallowed':
                state.balance -= candidates[0]['price']
                if state.mode == 'wrong_item':
                    state.cards[3]['status'] = 'owned'
                elif state.mode != 'coins_only':
                    candidates[0]['status'] = 'owned'
                if state.mode == 'wrong_debit': state.balance -= 1
                if state.mode == 'reward_timeout': state.kind = 'reward'
        return {'dry_run': False, 'reason': reason}

    def swipe(x0, y0, x1, y1, duration, reason=''):
        state.inputs.append((state.kind, state.offset, x1, y1, reason))
        if y0 > y1:
            if state.offset == 1100: state.catalogue_finished = True
            state.offset = min(1100, state.offset+500)
        else:
            if state.catalogue_finished and state.on_relocate is not None:
                callback, state.on_relocate = state.on_relocate, None
                callback()
            state.offset = max(0, state.offset-500)
        if state.mutate is not None:
            state.mutate()
        return {'dry_run': False}

    monkeypatch.setattr(act, 'tap', tap)
    monkeypatch.setattr(act, 'swipe', swipe)
    state.render = render
    state.content = content
    state.templates = templates
    return state


def run(world):
    runner = missions.Mission()
    runner.start(store.store_flow)
    for _ in range(180):
        runner.step(world.render())
        if not runner.active:
            return runner
    pytest.fail('store flow did not terminate within its offline bound')


def purchases(world):
    return [row for row in world.inputs if row[-1].startswith('store_buy_')]


def events(world, kind):
    return [data for name, data in world.events if name == kind]


def test_earlier_viewport_identity_is_relocated_and_outcome_is_proven(world):
    run(world)
    assert world.bought is world.target
    assert len(purchases(world)) == 1
    assert purchases(world)[0][1] == 500  # catalogue ended at 1100
    assert events(world, 'store_buy')[0]['proof'] == 'same_item_owned_and_exact_debit'
    assert events(world, 'store_buy')[0]['balance_after'] == 100
    assert not events(world, 'store_purchase_unverified')


@pytest.mark.parametrize('mode', ['swallowed', 'coins_only', 'wrong_item', 'wrong_debit',
                                 'transport', 'refused', 'dry'])
def test_ambiguous_dispatch_or_outcome_never_logs_success_or_retries(world, mode):
    world.mode = mode
    run(world)
    assert len(purchases(world)) == 1
    assert not events(world, 'store_buy')
    unknown = events(world, 'store_purchase_unverified')
    assert len(unknown) == 1 and unknown[0]['outcome'] == 'unknown'
    assert unknown[0]['retry'] is False


@pytest.mark.parametrize('balance', [None, 49])
def test_current_unreadable_or_insufficient_balance_prevents_dispatch(world, balance):
    world.on_relocate = lambda: setattr(world, 'balance', balance)
    run(world)
    assert purchases(world) == []
    assert events(world, 'store_saving')


def test_price_changed_after_catalogue_is_refused(world):
    world.on_relocate = lambda: world.target.update(price=75)
    run(world)
    assert purchases(world) == []
    assert 'price' in events(world, 'store_refused')[0]['reason']


def test_equal_price_same_category_replacement_is_not_the_selected_item(world):
    # A fresh chip is allowed by the ladder, but is not authorization to buy
    # a different chip after this flow has already chosen its concrete target.
    world.target['status'] = 'owned'
    world.target = world.cards[3]
    world.on_relocate = lambda: world.target.update(seed=27)
    run(world)
    assert purchases(world) == []
    assert not events(world, 'store_buy')


def test_same_category_candidates_are_retained_and_selected_concretely(world):
    world.target['status'] = 'owned'
    world.target = world.cards[3]
    run(world)
    assert world.bought is world.cards[3]
    assert world.cards[4]['status'] == 'price'
    assert len(purchases(world)) == 1


def test_duplicate_identity_in_distinct_physical_positions_is_refused(world):
    world.cards[4]['seed'] = world.cards[3]['seed']
    run(world)
    assert purchases(world) == []
    assert 'indistinguishable' in events(world, 'store_refused')[0]['reason']


def test_duplicate_identity_on_different_pages_is_not_merged(world):
    world.cards[4].update(seed=world.cards[3]['seed'], y=100)
    # These identical card interiors are never fully visible together. The
    # stitched catalogue must retain the physical positions and reject them.
    run(world)
    assert purchases(world) == []
    assert 'distinct store positions' in events(world, 'store_refused')[0]['reason']


def test_complete_unknown_state_cannot_hide_higher_priority_merchandise(world):
    world.target['status'] = 'unknown'  # includes unsupported SOLD OUT art
    run(world)
    assert purchases(world) == []
    assert 'no recognized' in events(world, 'store_refused')[0]['reason']


def test_missing_section_prevents_false_complete_catalogue(world, monkeypatch):
    real = store._header_ys
    monkeypatch.setattr(store, '_header_ys', lambda frame:
                        {k: v for k, v in real(frame).items() if k != 'relics'})
    run(world)
    assert purchases(world) == []
    assert events(world, 'store_refused')


def test_unrecognized_human_page_interrupts_without_spending(world):
    world.on_relocate = lambda: setattr(world, 'kind', 'cards')
    run(world)
    assert purchases(world) == []
    assert events(world, 'mission_interrupted')


def test_repeatable_without_item_transition_remains_unverified(world):
    for card in world.cards:
        card['status'] = 'owned'
    world.target = world.cards[0]
    world.target['status'] = 'price'
    world.mode = 'coins_only'
    # Verify the one-attempt outcome contract independently of eligibility:
    # the catalogue correctly refuses lower-priority gems without a calibrated
    # final-section extent, even when every currently visible chip is owned.
    guard = missions.FlowGuard()
    frame = world.render()
    guard.observe(frame)
    guard.tap(frame, 100, 100, 'guild_open', screen='battle_menu', destination='guild_entry')
    frame = world.render()
    guard.observe(frame)
    guard.tap(frame, *store.STORE_TAB, 'store_tab', screen='guild', destination='guild_store')
    frame = world.render()
    guard.observe(frame)
    target = next(item for item in store.scan_frame(frame).items if item.category == 'gems')
    action = store._purchase(guard, frame, target)
    next(action)
    for _ in range(100):
        try:
            action.send(world.render())
        except StopIteration:
            break
    else:
        pytest.fail('purchase outcome verifier did not terminate')
    assert len(purchases(world)) == 1
    assert events(world, 'store_purchase_unverified')
    assert not events(world, 'store_buy')


def test_confirmation_timeout_is_unknown_without_purchase_retry(world):
    world.mode = 'reward_timeout'
    run(world)
    assert len(purchases(world)) == 1
    assert not events(world, 'store_buy')
    assert events(world, 'store_purchase_unverified')[0]['reason'] == 'reward_confirmation_timeout'


def test_blank_card_identity_is_not_authority_to_spend(world):
    world.cards[3]['blank'] = True
    run(world)
    assert purchases(world) == []
    assert 'uninformative' in events(world, 'store_refused')[0]['reason']


def test_lost_page_overlap_is_not_catalogue_completion(world):
    world.kind = 'guild_store'
    before = world.render()
    after = np.random.default_rng(71).integers(0, 180, before.shape, dtype=np.uint8)
    with pytest.raises(store.StoreEvidenceError, match='overlap'):
        store._scroll_distance(before, after)


def test_changed_overlap_refuses_buying_before_end_of_scan(world):
    def change_art():
        for card in world.cards:
            card['seed'] += 20
    world.mutate = change_art
    run(world)
    assert purchases(world) == []
    assert events(world, 'store_refused')


def test_panel_geometry_does_not_scale_with_mosaic_height(world):
    image = world.content()
    # Native content band still defines the minimum panel height. All five
    # whole cards survive a stitched image taller than the input viewport.
    mosaic = np.zeros((4000, 480, 3), np.uint8)
    mosaic[120:2020] = image
    rects = store._card_rects(mosaic, (120, 2020))
    assert len(rects) == 5


def test_body_stability_ignores_balance_header(world):
    world.kind = 'guild_store'
    first = world.render()
    world.balance -= 50
    second = world.render()
    assert store._stable(first, second)


def test_clipped_control_is_explicit_and_not_a_purchase_target(world):
    world.kind = 'guild_store'
    world.offset = 1100
    observed = store.scan_frame(world.render())
    assert observed.partial_controls  # relic control is above a clipped panel
    assert all(item.category != 'epic_relic' for item in observed.items)


def test_stationary_truncated_final_section_cannot_authorize_lower_priority_purchase(world, monkeypatch):
    world.cards[2]['status'] = 'owned'
    world.cards[1]['status'] = 'price'
    world.target = world.cards[1]
    real_swipe = missions.act.swipe

    def clamped(*args, **kwargs):
        result = real_swipe(*args, **kwargs)
        world.offset = min(900, world.offset)
        return result

    monkeypatch.setattr(missions.act, 'swipe', clamped)
    run(world)
    assert purchases(world) == []
    assert world.cards[3]['status'] == world.cards[4]['status'] == 'price'
    assert 'coverage' in events(world, 'store_refused')[0]['reason']


@pytest.mark.parametrize('visible_height', [10, 40, 190])
def test_panel_fragment_without_visible_control_is_retained(world, visible_height):
    world.kind = 'guild_store'
    # The chip panel's upper edge appears; its price remains below the viewport.
    world.offset = world.cards[3]['y'] - 800 + visible_height
    observed = store.scan_frame(world.render())
    assert observed.partial_panels
    assert not any(item.category == 'chip' for item in observed.items)
    assert any(y < 920 <= y+h for _, y, _, h in observed.partial_panels)


def test_fully_stitched_catalogue_resolves_ordinary_edge_fragments(world):
    world.kind = 'guild_store'
    world.offset = 900
    assert store.scan_frame(world.render()).partial_panels
    mosaic = np.zeros((2020, 480, 3), np.uint8)
    mosaic[120:2020] = world.content()
    complete = store.scan_frame(mosaic, bounds=(120, 2020))
    assert complete.partial_panels == []
    assert len(complete.items) == 5


def test_owned_final_card_and_clean_margin_do_not_hide_unseen_priced_chip(world, monkeypatch):
    world.cards[2]['status'] = 'owned'
    world.cards[1]['status'] = 'price'
    world.target = world.cards[1]
    world.cards[3].update(status='owned', y=1470, h=200)
    world.cards[4].update(y=1740, h=100)
    real_swipe = missions.act.swipe

    def clamped(*args, **kwargs):
        result = real_swipe(*args, **kwargs)
        world.offset = min(900, world.offset)
        return result

    monkeypatch.setattr(missions.act, 'swipe', clamped)
    run(world)
    assert purchases(world) == []
    assert world.cards[4]['status'] == 'price'
    assert 'coverage' in events(world, 'store_refused')[0]['reason']


def test_wholly_unseen_final_merchandise_does_not_become_owned_from_header(world, monkeypatch):
    world.cards[2]['status'] = 'owned'
    world.cards[1]['status'] = 'price'
    real_swipe = missions.act.swipe

    def clamped(*args, **kwargs):
        result = real_swipe(*args, **kwargs)
        world.offset = min(700, world.offset)
        return result

    monkeypatch.setattr(missions.act, 'swipe', clamped)
    run(world)
    assert purchases(world) == []
    assert events(world, 'store_refused')


def test_positive_observed_chip_outranks_cosmetics_without_claiming_final_extent(world, monkeypatch):
    world.cards[2]['status'] = 'owned'
    world.cards[1]['status'] = 'price'
    world.target = world.cards[3]
    real_swipe = missions.act.swipe

    def clamped(*args, **kwargs):
        result = real_swipe(*args, **kwargs)
        world.offset = min(1000, world.offset)
        return result

    monkeypatch.setattr(missions.act, 'swipe', clamped)
    run(world)
    assert len(purchases(world)) == 1
    assert world.bought is world.cards[3]
    assert events(world, 'store_buy')


def test_catalogue_exposes_bounded_sections_and_never_certifies_final_extent(world):
    mosaic = np.zeros((2020, 480, 3), np.uint8)
    mosaic[120:2020] = world.content()
    catalogue = store._catalogue(mosaic, 120)
    assert catalogue.complete_sections == frozenset({'currencies', 'cosmetics', 'relics'})
    assert 'tower_guardian' not in catalogue.complete_sections


def test_card_crossing_a_section_boundary_cannot_certify_higher_priority_absence(world):
    world.cards[1].update(y=750, h=250)  # crosses the relics heading at y900
    mosaic = np.zeros((2020, 480, 3), np.uint8)
    mosaic[120:2020] = world.content()
    with pytest.raises(store.StoreEvidenceError):
        store._catalogue(mosaic, 120)
