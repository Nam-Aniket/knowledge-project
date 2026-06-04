#!/usr/bin/env python3
import sys
import os

# Ensure current directory is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def main():
    # Parse --topic and --profile out of sys.argv
    topic_name = None
    args_to_remove = []
    
    for idx, arg in enumerate(sys.argv):
        if arg in ("--topic", "--profile"):
            if idx + 1 < len(sys.argv):
                topic_name = sys.argv[idx + 1]
                args_to_remove.extend([idx, idx + 1])
            else:
                print(f"Error: {arg} requires a value.")
                sys.exit(1)
                
    # Remove these elements in reverse order to preserve indices
    for idx in sorted(args_to_remove, reverse=True):
        sys.argv.pop(idx)
        
    if topic_name:
        # Standardize topic database location in the 'data' directory
        os.environ["DATABASE_PATH"] = os.path.join("data", f"topic_{topic_name}.db")

    if len(sys.argv) < 2:
        print("Usage: psyche [setup | ingest | query | chat | build-graph | start-mcp] [options]")
        sys.exit(1)
        
    subcommand = sys.argv[1].lower()
    
    # Modify sys.argv to strip the subcommand name for sub-parsers
    sys.argv.pop(1)
    
    if subcommand == "setup":
        import setup_cmd
        setup_cmd.run_setup()
    elif subcommand == "ingest":
        import ingest
        ingest.main()
    elif subcommand == "query":
        import query
        query.main()
    elif subcommand == "chat":
        # Force chat mode by appending the flag
        sys.argv.append("--chat")
        import query
        query.main()
    elif subcommand == "build-graph":
        import build_graph
        build_graph.main()
    elif subcommand == "start-mcp":
        try:
            import mcp_server
            mcp_server.main()
        except ImportError:
            print("Error: mcp-server subcommand is not fully implemented yet.")
            sys.exit(1)
    else:
        print(f"Unknown command: {subcommand}")
        print("Available commands: setup, ingest, query, chat, build-graph, start-mcp")
        sys.exit(1)

if __name__ == "__main__":
    main()
