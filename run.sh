#!/bin/bash
#
# NM i AI - Main Entry Script
# Usage: ./run.sh [options]
#        ./run.sh --last          # Re-run last command
#        ./run.sh --history       # Show history and re-run
#

set -e

# Get script directory (project root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# History file
HISTORY_FILE="$SCRIPT_DIR/.run_history"
MAX_HISTORY=10

# Activate virtual environment if it exists
if [[ -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
else
    echo "Error: Virtual environment not found at .venv/"
    echo "Run: python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Available options
CHALLENGES=("grocery_bot")
BOTS=("theo" "mykyta" "member3")
DIFFICULTIES=("easy" "medium" "hard" "expert" "nightmare")

print_header() {
    clear
    echo -e "${CYAN}"
    echo "╔═══════════════════════════════════════════╗"
    echo "║           NM i AI - Runner                ║"
    echo "╚═══════════════════════════════════════════╝"
    echo -e "${NC}"
}

print_usage() {
    echo -e "${CYAN}NM i AI - Runner${NC}"
    echo ""
    echo -e "${YELLOW}Usage:${NC}"
    echo "  ./run.sh                    Interactive mode (default)"
    echo "  ./run.sh --last             Re-run last command"
    echo "  ./run.sh --history          Show history and select to re-run"
    echo ""
    echo -e "${YELLOW}Direct mode (skip interactive):${NC}"
    echo "  ./run.sh play -a -d easy    Run with auto-token, easy difficulty"
    echo "  ./run.sh test -b theo       Run tests for theo"
    echo ""
    echo -e "${YELLOW}Options:${NC}"
    echo "  -c, --challenge   Challenge name"
    echo "  -b, --bot         Bot implementation"
    echo "  -d, --difficulty  Game difficulty"
    echo "  -t, --token       Game token (manual mode)"
    echo "  -a, --auto-token  Fetch token automatically"
    echo "  -v, --verbose     Enable verbose logging"
    echo "  -o, --observe     Enable observation metrics"
    echo "  -h, --help        Show this help"
}

# Save command to history
save_to_history() {
    local cmd="$1"
    local timestamp=$(date "+%Y-%m-%d %H:%M")
    
    # Create history file if doesn't exist
    touch "$HISTORY_FILE"
    
    # Add new entry
    echo "$timestamp | $cmd" >> "$HISTORY_FILE"
    
    # Keep only last MAX_HISTORY entries
    tail -n $MAX_HISTORY "$HISTORY_FILE" > "$HISTORY_FILE.tmp"
    mv "$HISTORY_FILE.tmp" "$HISTORY_FILE"
}

# Get last command from history
get_last_command() {
    if [[ -f "$HISTORY_FILE" ]]; then
        tail -n 1 "$HISTORY_FILE" | sed 's/.*| //'
    else
        echo ""
    fi
}

# Show history and let user select
show_history() {
    print_header >&2
    echo -e "${YELLOW}Command History:${NC}" >&2
    echo "" >&2
    
    if [[ ! -f "$HISTORY_FILE" ]] || [[ ! -s "$HISTORY_FILE" ]]; then
        echo -e "${RED}No history found.${NC}" >&2
        exit 0
    fi
    
    # Read history into array (reverse order, newest first)
    mapfile -t history_lines < <(tac "$HISTORY_FILE")
    
    local i=1
    for line in "${history_lines[@]}"; do
        echo -e "  ${BOLD}$i)${NC} $line" >&2
        ((i++))
    done
    
    echo "" >&2
    echo -e "  ${BOLD}q)${NC} Quit" >&2
    echo "" >&2
    printf "Re-run which? [1-$((i-1)), q]: " >&2
    read -r choice
    
    if [[ "$choice" == "q" ]] || [[ "$choice" == "Q" ]]; then
        exit 0
    fi
    
    if [[ "$choice" =~ ^[0-9]+$ ]] && [[ "$choice" -ge 1 ]] && [[ "$choice" -le ${#history_lines[@]} ]]; then
        # Get the command part (after the |)
        local selected="${history_lines[$((choice-1))]}"
        local cmd=$(echo "$selected" | sed 's/.*| //')
        echo "" >&2
        echo -e "${GREEN}Re-running: $cmd${NC}" >&2
        eval "$cmd"
    else
        echo -e "${RED}Invalid choice.${NC}" >&2
        exit 1
    fi
}

# Interactive selection menu
select_option() {
    local prompt="$1"
    shift
    local options=("$@")
    
    echo -e "${YELLOW}$prompt${NC}" >&2
    local i=1
    for opt in "${options[@]}"; do
        echo -e "  ${BOLD}$i)${NC} $opt" >&2
        ((i++))
    done
    echo "" >&2
    printf "Select [1-${#options[@]}]: " >&2
    read -r choice
    
    if [[ "$choice" =~ ^[0-9]+$ ]] && [[ "$choice" -ge 1 ]] && [[ "$choice" -le ${#options[@]} ]]; then
        echo "${options[$((choice-1))]}"
    else
        echo -e "${RED}Invalid choice, using default.${NC}" >&2
        echo "${options[0]}"
    fi
}

# Yes/No prompt
prompt_yes_no() {
    local prompt="$1"
    local default="${2:-n}"
    
    local default_display="y/N"
    [[ "$default" == "y" ]] && default_display="Y/n"
    
    printf "%s [%s]: " "$prompt" "$default_display" >&2
    read -r response
    
    response=${response:-$default}
    [[ "$response" =~ ^[yY] ]]
}

# Interactive mode
interactive_mode() {
    print_header
    
    # Select challenge
    echo ""
    CHALLENGE=$(select_option "Select challenge:" "${CHALLENGES[@]}")
    
    # Select bot
    echo ""
    BOT=$(select_option "Select bot:" "${BOTS[@]}")
    
    # Select difficulty
    echo ""
    DIFFICULTY=$(select_option "Select difficulty:" "${DIFFICULTIES[@]}")
    
    # Token mode
    echo ""
    echo -e "${YELLOW}Token mode:${NC}"
    echo -e "  ${BOLD}1)${NC} Auto (opens browser)"
    echo -e "  ${BOLD}2)${NC} Manual (paste token)"
    echo ""
    printf "Select [1-2]: "
    read -r token_choice
    
    if [[ "$token_choice" == "2" ]]; then
        echo ""
        printf "Enter token: "
        read -r TOKEN
        AUTO_TOKEN=""
    else
        AUTO_TOKEN="1"
        TOKEN=""
    fi
    
    # Options
    echo ""
    OBSERVE=$(prompt_yes_no "Enable observation?" "n") && OBSERVE="1" || OBSERVE=""
    VERBOSE=$(prompt_yes_no "Enable verbose logging?" "n") && VERBOSE="1" || VERBOSE=""
    
    # Confirm
    echo ""
    echo -e "${CYAN}═══════════════════════════════════════════${NC}"
    echo -e "${BOLD}Summary:${NC}"
    echo -e "  Challenge:  ${GREEN}$CHALLENGE${NC}"
    echo -e "  Bot:        ${GREEN}$BOT${NC}"
    echo -e "  Difficulty: ${GREEN}$DIFFICULTY${NC}"
    echo -e "  Token:      ${GREEN}$([ -n "$AUTO_TOKEN" ] && echo "auto" || echo "manual")${NC}"
    echo -e "  Observe:    ${GREEN}$([ -n "$OBSERVE" ] && echo "yes" || echo "no")${NC}"
    echo -e "  Verbose:    ${GREEN}$([ -n "$VERBOSE" ] && echo "yes" || echo "no")${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════${NC}"
    echo ""
    
    if ! prompt_yes_no "Start run?" "y"; then
        echo "Cancelled."
        exit 0
    fi
    
    # Build and save command
    local cmd="cd $SCRIPT_DIR && ./run.sh play -c $CHALLENGE -b $BOT -d $DIFFICULTY"
    [[ -n "$AUTO_TOKEN" ]] && cmd="$cmd -a"
    [[ -n "$TOKEN" ]] && cmd="$cmd -t $TOKEN"
    [[ -n "$OBSERVE" ]] && cmd="$cmd -o"
    [[ -n "$VERBOSE" ]] && cmd="$cmd -v"
    
    save_to_history "$cmd"
    
    # Run
    echo ""
    cmd_play
}

run_python() {
    PYTHONPATH="$SCRIPT_DIR" python "$@"
}

cmd_play() {
    local args=()
    
    if [[ -n "$AUTO_TOKEN" ]]; then
        args+=("--auto-token")
    fi
    if [[ -n "$TOKEN" ]]; then
        args+=("--token" "$TOKEN")
    fi
    args+=("--bot" "$BOT")
    args+=("--difficulty" "$DIFFICULTY")
    args+=("--challenge" "$CHALLENGE")
    if [[ -n "$VERBOSE" ]]; then
        args+=("--verbose")
    fi
    if [[ -n "$OBSERVE" ]]; then
        args+=("--observe")
    fi
    
    echo -e "${GREEN}Starting ${BOT}'s bot for ${CHALLENGE} (${DIFFICULTY})...${NC}"
    run_python main.py "${args[@]}"
}

cmd_test() {
    echo -e "${GREEN}Running tests for ${BOT}...${NC}"
    
    case "$CHALLENGE" in
        "grocery_bot")
            local test_path="challenges/grocery_bot/$BOT/tests/"
            if [[ -d "$test_path" ]]; then
                PYTHONPATH="$SCRIPT_DIR" pytest "$test_path" -v
            else
                echo -e "${RED}No tests found for $BOT in $CHALLENGE${NC}"
                exit 1
            fi
            ;;
        *)
            echo -e "${RED}Unknown challenge: $CHALLENGE${NC}"
            exit 1
            ;;
    esac
}

cmd_bench() {
    echo -e "${GREEN}Running performance benchmarks...${NC}"
    PYTHONPATH="$SCRIPT_DIR" python testing/test_performance.py --difficulty "$DIFFICULTY"
}

cmd_maps() {
    echo -e "${GREEN}Fetching available maps...${NC}"
    PYTHONPATH="$SCRIPT_DIR" python -c "
import asyncio
from tools.token_manager import TokenManager

async def main():
    tm = TokenManager()
    maps = await tm.get_available_maps()
    for m in maps:
        print(f'  - {m}')

asyncio.run(main())
"
}

# Default values
CHALLENGE="grocery_bot"
BOT="theo"
DIFFICULTY="medium"
VERBOSE=""
OBSERVE=""
TOKEN=""
AUTO_TOKEN=""

# Check for special flags first
if [[ "$1" == "--last" ]]; then
    last_cmd=$(get_last_command)
    if [[ -z "$last_cmd" ]]; then
        echo -e "${RED}No history found.${NC}"
        exit 1
    fi
    echo -e "${GREEN}Re-running: $last_cmd${NC}"
    eval "$last_cmd"
    exit $?
fi

if [[ "$1" == "--history" ]]; then
    show_history
    exit $?
fi

if [[ "$1" == "-h" ]] || [[ "$1" == "--help" ]]; then
    print_usage
    exit 0
fi

# If no args, run interactive mode
if [[ $# -eq 0 ]]; then
    interactive_mode
    exit $?
fi

# Parse arguments for direct mode
COMMAND="play"
while [[ $# -gt 0 ]]; do
    case $1 in
        play|test|bench|maps)
            COMMAND="$1"
            shift
            ;;
        -c|--challenge)
            CHALLENGE="$2"
            shift 2
            ;;
        -b|--bot)
            BOT="$2"
            shift 2
            ;;
        -d|--difficulty)
            DIFFICULTY="$2"
            shift 2
            ;;
        -t|--token)
            TOKEN="$2"
            shift 2
            ;;
        -a|--auto-token)
            AUTO_TOKEN="1"
            shift
            ;;
        -v|--verbose)
            VERBOSE="1"
            shift
            ;;
        -o|--observe)
            OBSERVE="1"
            shift
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            print_usage
            exit 1
            ;;
    esac
done

# Validate options
if [[ ! " ${CHALLENGES[*]} " =~ " ${CHALLENGE} " ]]; then
    echo -e "${RED}Invalid challenge: $CHALLENGE${NC}"
    echo "Available: ${CHALLENGES[*]}"
    exit 1
fi

if [[ ! " ${BOTS[*]} " =~ " ${BOT} " ]]; then
    echo -e "${RED}Invalid bot: $BOT${NC}"
    echo "Available: ${BOTS[*]}"
    exit 1
fi

if [[ ! " ${DIFFICULTIES[*]} " =~ " ${DIFFICULTY} " ]]; then
    echo -e "${RED}Invalid difficulty: $DIFFICULTY${NC}"
    echo "Available: ${DIFFICULTIES[*]}"
    exit 1
fi

# Save to history for direct mode (only for play command)
if [[ "$COMMAND" == "play" ]]; then
    HISTORY_CMD="cd $SCRIPT_DIR && ./run.sh play -c $CHALLENGE -b $BOT -d $DIFFICULTY"
    [[ -n "$AUTO_TOKEN" ]] && HISTORY_CMD="$HISTORY_CMD -a"
    [[ -n "$TOKEN" ]] && HISTORY_CMD="$HISTORY_CMD -t $TOKEN"
    [[ -n "$OBSERVE" ]] && HISTORY_CMD="$HISTORY_CMD -o"
    [[ -n "$VERBOSE" ]] && HISTORY_CMD="$HISTORY_CMD -v"
    save_to_history "$HISTORY_CMD"
fi

# Execute command
case "$COMMAND" in
    play)
        cmd_play
        ;;
    test)
        cmd_test
        ;;
    bench)
        cmd_bench
        ;;
    maps)
        cmd_maps
        ;;
    *)
        echo -e "${RED}Unknown command: $COMMAND${NC}"
        print_usage
        exit 1
        ;;
esac
