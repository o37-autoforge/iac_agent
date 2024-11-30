from setup_agent import start_setup
from code_loop import start_code_loop

initial_state, subprocess_handler, forge_interface = start_setup()
# post_coding_state = start_code_loop(initial_state, forge_interface)