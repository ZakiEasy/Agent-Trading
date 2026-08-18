#!/usr/bin/env python3
import sys
from src.cli import run_analyze_ticker, run_scan_watchlist, run_scan_market, run_check_macro, run_add_ticker_to_watchlist

def print_usage():
    print("Assistant Swing Trading v2.0 - Usage:")
    print("  python run.py scan watchlist  : Scanne la watchlist Google Sheets (ou défaut)")
    print("  python run.py scan market     : Scanne le marché élargi (Large & Mid Caps)")
    print("  python run.py analyze <TICKER>: Lance le protocole en 8 étapes pour un ticker")
    print("  python run.py add <TICKER>    : Ajoute une action au Google Sheet et l'analyse")
    print("  python run.py check macro     : Affiche le Baromètre Macroéconomique Top-Down")
    print("\nVariantes courtes :")
    print("  python run.py scan-watchlist")
    print("  python run.py scan-market")
    print("  python run.py check-macro")

def main():
    args = sys.argv[1:]
    if not args:
        print_usage()
        return

    cmd = args[0].lower()
    
    if cmd == "scan" and len(args) > 1:
        subcmd = args[1].lower()
        if subcmd == "watchlist":
            run_scan_watchlist()
        elif subcmd == "market":
            run_scan_market()
        else:
            print(f"❌ Sous-commande de scan inconnue : {args[1]}")
            print_usage()
    elif cmd == "check" and len(args) > 1:
        subcmd = args[1].lower()
        if subcmd == "macro":
            run_check_macro()
        else:
            print(f"❌ Sous-commande de check inconnue : {args[1]}")
            print_usage()
    elif cmd == "analyze" and len(args) > 1:
        run_analyze_ticker(args[1])
    elif cmd == "add" and len(args) > 1:
        run_add_ticker_to_watchlist(args[1])
    elif cmd == "scan-watchlist":
        run_scan_watchlist()
    elif cmd == "scan-market":
        run_scan_market()
    elif cmd == "check-macro":
        run_check_macro()
    else:
        print(f"❌ Commande inconnue : {' '.join(args)}")
        print_usage()

if __name__ == "__main__":
    main()
