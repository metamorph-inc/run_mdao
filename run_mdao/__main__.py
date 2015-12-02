import sys
import run_mdao

if __name__ == '__main__':
    run_mdao.run('mdao_config.json' if len(sys.argv) <= 1 else sys.argv[1])
