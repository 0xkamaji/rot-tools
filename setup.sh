#!/bin/sh

set -eu

project_dir="$(CDPATH= cd "$(dirname "$0")" && pwd)"
bin_dir="${HOME}/.local/bin"
script_path="${project_dir}/rotbot.py"

show_rotbot() {
    printf '%s\n' \
        '   .-.' \
        '  [x_o]' \
        '  /|%|\' \
        '   / \' \
        '  ROTBOT'
}

confirm() {
    printf '%s [y/N] ' "$1"
    IFS= read -r answer || answer='n'

    case "${answer}" in
        y|Y|yes|YES|Yes) return 0 ;;
        *) return 1 ;;
    esac
}

line_exists() {
    expected_line="$1"
    config_file="$2"

    [ -f "${config_file}" ] || return 1

    while IFS= read -r config_line || [ -n "${config_line}" ]; do
        [ "${config_line}" = "${expected_line}" ] && return 0
    done < "${config_file}"

    return 1
}

install_command() {
    command_name="$1"
    command_path="${bin_dir}/${command_name}"

    if [ -e "${command_path}" ] || [ -L "${command_path}" ]; then
        printf '\n%s is already installed at %s.\n' "${command_name}" "${command_path}"

        if [ -x "${command_path}" ] && "${command_path}" --help >/dev/null 2>&1; then
            printf 'The installed %s command is working.\n' "${command_name}"
        else
            printf 'The installed %s command is not currently working.\n' "${command_name}"
        fi

        installed_target="$(readlink "${command_path}" 2>/dev/null || true)"

        if [ "${installed_target}" = "${script_path}" ]; then
            printf '%s already points to this copy, so it is up to date.\n' "${command_name}"
        elif [ -L "${command_path}" ]; then
            printf 'Installed copy: %s\n' "${installed_target:-unknown}"
            printf 'Current copy:   %s\n' "${script_path}"

            if confirm "Would you like to update ${command_name} to this copy?"; then
                replacement_link="${command_path}.tmp.$$"
                ln -s "${script_path}" "${replacement_link}"
                mv -f "${replacement_link}" "${command_path}"
                printf 'Updated %s at %s.\n' "${command_name}" "${command_path}"
            else
                printf 'Keeping the existing %s installation.\n' "${command_name}"
            fi
        else
            printf 'The existing %s command is not a symlink, so it will not be overwritten automatically.\n' "${command_name}" >&2
        fi
    else
        ln -s "${script_path}" "${command_path}"
        printf '\nInstalled %s at %s.\n' "${command_name}" "${command_path}"
    fi
}

shell_name="${SHELL##*/}"

show_rotbot
printf '\nStarting local Rotbot setup.\n'
printf 'Detected your configured shell: %s (%s)\n' "${shell_name:-unknown}" "${SHELL:-not set}"

case "${shell_name}" in
    bash)
        shell_config="${HOME}/.bashrc"
        path_line='export PATH="$HOME/.local/bin:$PATH"'
        ;;
    zsh)
        shell_config="${HOME}/.zshrc"
        path_line='export PATH="$HOME/.local/bin:$PATH"'
        ;;
    fish)
        shell_config="${HOME}/.config/fish/config.fish"
        path_line='set -gx PATH $HOME/.local/bin $PATH'
        mkdir -p "${HOME}/.config/fish"
        ;;
    *)
        printf 'Shell %s is not supported for automatic PATH setup.\n' "${shell_name:-unknown}" >&2
        exit 0
        ;;
esac

mkdir -p "${bin_dir}"

install_command rotbot
install_command rot

if line_exists "${path_line}" "${shell_config}"; then
    printf '%s is already configured in %s.\n' "${bin_dir}" "${shell_config}"
else
    if [ -s "${shell_config}" ]; then
        printf '\n%s\n' "${path_line}" >> "${shell_config}"
    else
        printf '%s\n' "${path_line}" >> "${shell_config}"
    fi
    printf 'Added %s to PATH in %s.\n' "${bin_dir}" "${shell_config}"
fi

if confirm "Would you like to reload ${shell_name} now?"; then
    printf 'Starting a refreshed %s shell. Type exit to return to your previous shell.\n' "${shell_name}"
    exec "${SHELL}" -i
else
    printf 'Open a new terminal when you are ready to use the rotbot command.\n'
fi
