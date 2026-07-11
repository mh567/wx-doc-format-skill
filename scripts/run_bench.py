#!/usr/bin/env python3
"""Simple runner for benchmark.py — redirects all args."""
import sys, runpy
sys.argv = ['benchmark.py'] + sys.argv[1:]
runpy.run_path('benchmark.py', run_name='__main__')
