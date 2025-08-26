def reformat_name(name):
    parts = name.split(', ')
    last_name = parts[0]
    first_middle_name = parts[1]
    first_middle_parts = first_middle_name.split(' ')
    first_name = first_middle_parts[0]
    return f"{first_name} {last_name}"

name = "Xu, Brandon L"
formatted_name = reformat_name(name)
print(formatted_name)
