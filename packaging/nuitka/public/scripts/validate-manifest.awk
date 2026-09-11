{
    json = json $0 "\n"
}

END {
    json_length = length(json)
    position = 1
    if (!parse_value()) {
        exit 1
    }
    skip_whitespace()
    if (position <= json_length) {
        exit 1
    }
    exit 0
}

function skip_whitespace(    character) {
    while (position <= json_length) {
        character = substr(json, position, 1)
        if (character !~ /[ \t\r\n]/) {
            return
        }
        position++
    }
}

function parse_value(    character) {
    skip_whitespace()
    character = substr(json, position, 1)
    if (character == "{") {
        return parse_object()
    }
    if (character == "[") {
        return parse_array()
    }
    if (character == "\"") {
        return parse_string()
    }
    if (substr(json, position, 4) == "true") {
        position += 4
        return 1
    }
    if (substr(json, position, 5) == "false") {
        position += 5
        return 1
    }
    if (substr(json, position, 4) == "null") {
        position += 4
        return 1
    }
    return parse_number()
}

function parse_object(    character) {
    position++
    skip_whitespace()
    if (substr(json, position, 1) == "}") {
        position++
        return 1
    }
    while (position <= json_length) {
        if (!parse_string()) {
            return 0
        }
        skip_whitespace()
        if (substr(json, position, 1) != ":") {
            return 0
        }
        position++
        if (!parse_value()) {
            return 0
        }
        skip_whitespace()
        character = substr(json, position, 1)
        if (character == "}") {
            position++
            return 1
        }
        if (character != ",") {
            return 0
        }
        position++
        skip_whitespace()
    }
    return 0
}

function parse_array(    character) {
    position++
    skip_whitespace()
    if (substr(json, position, 1) == "]") {
        position++
        return 1
    }
    while (position <= json_length) {
        if (!parse_value()) {
            return 0
        }
        skip_whitespace()
        character = substr(json, position, 1)
        if (character == "]") {
            position++
            return 1
        }
        if (character != ",") {
            return 0
        }
        position++
    }
    return 0
}

function parse_string(    character, escaped, hexadecimal) {
    skip_whitespace()
    if (substr(json, position, 1) != "\"") {
        return 0
    }
    position++
    while (position <= json_length) {
        character = substr(json, position, 1)
        if (character == "\"") {
            position++
            return 1
        }
        if (character == "\\") {
            position++
            if (position > json_length) {
                return 0
            }
            escaped = substr(json, position, 1)
            if (escaped == "u") {
                hexadecimal = substr(json, position + 1, 4)
                if (length(hexadecimal) != 4 || hexadecimal !~ /^[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]$/) {
                    return 0
                }
                position += 5
                continue
            }
            if (!(escaped == "\"" || escaped == "\\" || escaped == "/" ||
                  escaped == "b" || escaped == "f" || escaped == "n" ||
                  escaped == "r" || escaped == "t")) {
                return 0
            }
            position++
            continue
        }
        if (character ~ /[[:cntrl:]]/) {
            return 0
        }
        position++
    }
    return 0
}

function parse_number(    start, character, number) {
    start = position
    while (position <= json_length) {
        character = substr(json, position, 1)
        if (character !~ /[0-9eE+.-]/) {
            break
        }
        position++
    }
    if (position == start) {
        return 0
    }
    number = substr(json, start, position - start)
    return number ~ /^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$/
}
