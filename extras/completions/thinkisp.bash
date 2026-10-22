# ==============================================================================
# ThinkISP - Autocompletado para GNU Bash
# ==============================================================================
# Para activar en tu sesión:
#   source ./extras/completions/thinkisp.bash
#
# Para instalación permanente:
#   sudo cp ./extras/completions/thinkisp.bash /etc/bash_completion.d/thinkisp
#   O cópialo a ~/.local/share/bash-completion/completions/thinkisp
# ==============================================================================

_thinkisp_completions() {
    local cur prev words cword
    if declare -F _init_completion >/dev/null 2>&1; then
        _init_completion || return
    else
        COMPREPLY=()
        cur="${COMP_WORDS[COMP_CWORD]}"
        prev="${COMP_WORDS[COMP_CWORD-1]}"
        words=("${COMP_WORDS[@]}")
        cword=$COMP_CWORD
    fi

    local commands="portal network doctor setup config on off recover boost status clients speed top stats widget kick unkick blocked stealth timer gaming limit adblock dns qr band toggle tui"
    local global_flags="-h --help"

    # Nivel 1: Subcomandos u opciones globales
    if [[ $cword -eq 1 ]]; then
        if [[ "$cur" == -* ]]; then
            COMPREPLY=($(compgen -W "$global_flags" -- "$cur"))
        else
            COMPREPLY=($(compgen -W "$commands" -- "$cur"))
        fi
        return 0
    fi

    # Nivel 2+: Opciones específicas por subcomando
    local cmd="${words[1]}"

    case "$cmd" in
        portal)
            case "$prev" in
                authorize)
                    local clients=""
                    if command -v thinkisp &>/dev/null; then
                        clients=$(thinkisp clients 2>/dev/null | awk '/([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}/ {for(i=1;i<=NF;i++) if($i ~ /^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$/ || $i ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) print $i}')
                    fi
                    COMPREPLY=($(compgen -W "$clients" -- "$cur"))
                    ;;
                revoke)
                    local clients=""
                    if command -v thinkisp &>/dev/null; then
                        clients=$(thinkisp portal status --json 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+')
                    fi
                    COMPREPLY=($(compgen -W "$clients" -- "$cur"))
                    ;;
                status)
                    COMPREPLY=($(compgen -W "--json --help" -- "$cur"))
                    ;;
                *)
                    COMPREPLY=($(compgen -W "status enable disable authorize revoke --json --help" -- "$cur"))
                    ;;
            esac
            ;;
        network)
            case "$prev" in
                --mode) COMPREPLY=($(compgen -W "auto routed repeater" -- "$cur")) ;;
                --engine) COMPREPLY=($(compgen -W "auto none builtin opennds" -- "$cur")) ;;
                --ap|--upstream) COMPREPLY=($(compgen -W "auto" -- "$cur")) ;;
                *) COMPREPLY=($(compgen -W "inspect plan --ap --upstream --mode --engine --save --json --help" -- "$cur")) ;;
            esac
            ;;
        doctor)
            COMPREPLY=($(compgen -W "--json --help" -- "$cur"))
            ;;
        widget|json)
            COMPREPLY=($(compgen -W "-w --watch -h --help" -- "$cur"))
            ;;

        stealth)
            COMPREPLY=($(compgen -W "on off status" -- "$cur"))
            ;;

        gaming)
            COMPREPLY=($(compgen -W "on off status toggle" -- "$cur"))
            ;;

        adblock)
            COMPREPLY=($(compgen -W "on off status toggle" -- "$cur"))
            ;;

        dns)
            COMPREPLY=($(compgen -W "cloudflare google quad9 adguard default auto restore benchmark" -- "$cur"))
            ;;

        band)
            COMPREPLY=($(compgen -W "5 5g 2.4 2.4g auto" -- "$cur"))
            ;;

        limit)
            COMPREPLY=($(compgen -W "reset 10m 20m 30m 50m 100m" -- "$cur"))
            ;;

        timer)
            if [[ $cword -eq 2 ]]; then
                COMPREPLY=($(compgen -W "cancel idle status 15m 30m 45m 1h 2h" -- "$cur"))
            elif [[ $cword -eq 3 && "${words[2]}" == "idle" ]]; then
                COMPREPLY=($(compgen -W "5 10 15 20 30" -- "$cur"))
            fi
            ;;

        kick)
            # Autocompletar con IPs o MACs de clientes actualmente conectados
            local clients=""
            if command -v thinkisp &>/dev/null; then
                clients=$(thinkisp clients 2>/dev/null | awk '/([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}/ {for(i=1;i<=NF;i++) if($i ~ /^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$/ || $i ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) print $i}')
            fi
            COMPREPLY=($(compgen -W "$clients" -- "$cur"))
            ;;

        unkick|unblock)
            # Autocompletar con MACs actualmente en lista negra
            local blocked=""
            if command -v thinkisp &>/dev/null; then
                blocked=$(thinkisp blocked 2>/dev/null | awk '/([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}/ {for(i=1;i<=NF;i++) if($i ~ /^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$/) print $i}')
            fi
            COMPREPLY=($(compgen -W "$blocked" -- "$cur"))
            ;;

        setup|config|on|off|boost|status|clients|speed|top|watch|stats|blocked|banlist|qr|toggle|tui)
            if [[ "$cur" == -* ]]; then
                COMPREPLY=($(compgen -W "$global_flags" -- "$cur"))
            fi
            ;;

        *)
            ;;
    esac

    return 0
}

# Registrar para 'thinkisp' y el alias común 'isp'
complete -F _thinkisp_completions thinkisp
complete -F _thinkisp_completions isp
