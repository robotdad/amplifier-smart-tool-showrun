"""Public-library terminal trial against a caller-prepared, empty native prompt.

Invoke --help for required target and outcome details. Does not launch a terminal,
install the companion, grant permissions, or prepare target-agent authentication.
"""
import argparse
import json
from pathlib import Path

from amplifier_smart_tool_showrun import Showrun


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle-id', 'starting-state', 'command', 'ready-text', 'request-id'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--window-title', help='Omit to bind the app’s only eligible window.')
    parser.add_argument('--storage', type=Path, required=True)
    parser.add_argument('--provider', default='openai')
    parser.add_argument('--model', default='gpt-4.1')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    request = {
        'request_id': args.request_id,
        'target': {'kind': 'macos', 'bundle_id': args.bundle_id, 'window_title': args.window_title,
                   'input_mode': 'terminal', 'resize_to_capture': True},
        'starting_state': args.starting_state, 'capture': {'width': 1280, 'height': 720},
        'steps': [
            {'id': 'type', 'instruction': 'Type the exact permitted command once, without submitting.',
             'visible_text': args.command, 'hold_seconds': 3},
            {'id': 'launch', 'instruction': 'Press Enter once to submit, then wait for the application.',
             'visible_text': args.ready_text, 'hold_seconds': 5},
        ],
        'authority': {'navigation_only': False, 'disclose_dom': False,
                      'disclose_accessibility': True, 'disclose_screenshots': True,
                      'max_seconds': 120, 'max_actions': 8, 'max_model_calls': 8,
                      'ui': {'actions': ['type', 'key'], 'allowed_values': [args.command],
                             'allowed_keys': ['Enter'], 'target_effects': 'all_in_session'}},
    }
    if args.window_title is None:
        del request['target']['window_title']
    api = Showrun(storage=args.storage, model={'provider': args.provider, 'model': args.model})
    result = api.validate(request) if args.validate_only else api.record(request)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
