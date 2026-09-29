"""kira_scheduler: the kira-earn program that runs the bot workflows on Kira instead of GitHub Actions.

Stdlib only, so it starts even when the bot's own dependencies are broken. The three workflows it
replaces are ``run_bot_on_tournament``, ``run_bot_on_minibench`` and ``run_bot_on_mantic``; ``spec``
holds their slots, gate keys, command and env, and ``tests/test_kira_scheduler_spec.py`` pins that
table to the workflow YAML so the two cannot drift apart. Contract: kira-earn's SPEC.md.
"""
