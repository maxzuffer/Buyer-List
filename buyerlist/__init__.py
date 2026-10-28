"""Buyer-list builder for Clairfield sell-side longlists (reference longlist format).

Every step is a gated stage (see state.py and gates.py). A stage can only start
when the previous stage's gate has passed and its outputs are unchanged.
Run from the project folder:  .venv/bin/python -m buyerlist <command>
"""
__version__ = "1.0.0"
