#!/usr/bin/env python3
import os
import sys
import sqlite3
import subprocess
from datetime import datetime, timezone, timedelta

# Ensure current directory is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import get_connection, resolve_db_path, get_metadata, set_metadata
from llm_client import LLMClient

def compile_daily_logs(db_path_arg: str = None, force: bool = False):
    db_path = resolve_db_path(db_path_arg or os.getenv("DATABASE_PATH", "knowledge.db"))
    if not os.path.exists(db_path):
        print(f"[Psyche Compactor] Database not found at '{db_path}'.")
        return
        
    conn = get_connection(db_path)
    try:
        # Get last compaction timestamp
        last_compaction = get_metadata(conn, "last_compaction_timestamp")
        
        # Query logs
        cursor = conn.cursor()
        if last_compaction and not force:
            cursor.execute("""
                SELECT role, content, created_at 
                FROM memory_recall 
                WHERE created_at > ? 
                ORDER BY created_at ASC
            """, (last_compaction,))
        else:
            # Default to last 7 days to compile recent history if first time or forced
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            cursor.execute("""
                SELECT role, content, created_at 
                FROM memory_recall 
                WHERE created_at >= ? 
                ORDER BY created_at ASC
            """, (cutoff,))
            
        logs = cursor.fetchall()
        
        if not logs:
            print("[Psyche Compactor] No new interaction logs found since last compaction.")
            return
            
        # Format transcript
        transcript_lines = []
        max_timestamp = last_compaction
        for role, content, timestamp in logs:
            transcript_lines.append(f"[{timestamp}] {role.upper()}: {content}")
            if not max_timestamp or timestamp > max_timestamp:
                max_timestamp = timestamp
                
        transcript = "\n".join(transcript_lines)
        
        # Initialize LLM Client
        llm = LLMClient()
        if llm.provider == "none" or llm.chat_model == "none":
            print("[Psyche Compactor] LLM provider or chat model is not configured. Skipping synthesis.")
            return
            
        print(f"[Psyche Compactor] Found {len(logs)} messages. Compiling memories...")
        
        system_instruction = (
            "You are an expert developer memory compiler. Review the transcript of the agent's recent interactions with the developer.\n"
            "Extract:\n"
            "1. Important architectural decisions, gotchas, or bugs resolved.\n"
            "2. Coding preferences and project setup rules.\n"
            "3. Lessons learned that future assistant sessions should remember.\n\n"
            "Format the output as a clear, structured markdown document. Focus only on generalizable knowledge "
            "(e.g. rules, configurations, commands) that will prevent repeating past debugging work."
        )
        
        compilation = llm.generate_completion(system_instruction, f"Transcript:\n{transcript}")
        
        # Create memories folder in ~/.psyche/
        home_dir = os.path.expanduser("~/.psyche")
        memories_dir = os.path.join(home_dir, "memories")
        os.makedirs(memories_dir, exist_ok=True)
        
        timestamp_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        memory_filename = f"synthesis_{timestamp_str}.md"
        memory_path = os.path.join(memories_dir, memory_filename)
        
        with open(memory_path, "w", encoding="utf-8") as f:
            f.write(f"# [Daily Learning Synthesis] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(compilation)
            
        print(f"[Psyche Compactor] Wrote learning synthesis to '{memory_path}'.")
        
        # Update metadata
        if max_timestamp:
            set_metadata(conn, "last_compaction_timestamp", max_timestamp)
            
        # Ingest the new memory file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        ingest_script = os.path.join(current_dir, "ingest.py")
        print(f"[Psyche Compactor] Ingesting memory file...")
        cmd = [sys.executable, ingest_script, memory_path]
        if db_path_arg:
            cmd.extend(["--db-path", db_path_arg])
        subprocess.run(cmd, check=True)
        print("[Psyche Compactor] Memory file successfully ingested and indexed.")
        
    except Exception as e:
        print(f"[Psyche Compactor] Error running memory compaction: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

if __name__ == "__main__":
    compile_daily_logs()
