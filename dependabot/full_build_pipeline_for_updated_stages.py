import json
import requests
import constants
import os
import sys

dist_repo_patch_branch = '2201.0.x'
swan_lake_update_number = 0

stdlib_modules_by_level = dict()
test_ignore_modules = []
build_ignore_modules = []
stdlib_modules_json_file = 'https://raw.githubusercontent.com/ballerina-platform/ballerina-release/master/' + \
                           'dependabot/resources/extensions.json'
test_ignore_modules_file = 'dependabot/resources/full_build_ignore_modules.json'

ballerina_lang_branch = "master"
enable_tests = 'true'
github_user = 'ballerina-platform'
exit_code = 0

ballerina_bot_username = os.environ[constants.ENV_BALLERINA_BOT_USERNAME]
ballerina_bot_token = os.environ[constants.ENV_BALLERINA_BOT_TOKEN]


def main():
    global stdlib_modules_by_level
    global stdlib_modules_json_file
    global test_ignore_modules_file
    global test_ignore_modules
    global build_ignore_modules
    global ballerina_lang_branch
    global github_user
    global dist_repo_patch_branch
    global swan_lake_update_number
    global enable_tests

    if len(sys.argv) > 3:
        ballerina_lang_branch = sys.argv[1]
        enable_tests = sys.argv[2]
        github_user = sys.argv[3]
        dist_repo_patch_branch = sys.argv[4]
        try:
            swan_lake_update_number = int(dist_repo_patch_branch.split(".")[1])
        except IndexError:
            print("Pipeline is using master branches of downstream repositories")

    read_stdlib_modules()
    read_ignore_modules()
    if stdlib_modules_by_level:
        clone_repositories()
        switch_to_branches_from_updated_stages()
        change_version_to_snapshot()
        build_stdlib_repositories(enable_tests)
    else:
        print('Could not find standard library dependency data from', stdlib_modules_json_file)


def read_ignore_modules():
    global test_ignore_modules
    global build_ignore_modules

    try:
        file = open(test_ignore_modules_file)
        data = json.load(file)
        test_ignore_modules = data[dist_repo_patch_branch]['test-ignore-modules']
        build_ignore_modules = data[dist_repo_patch_branch]['build-ignore-modules']

    except json.decoder.JSONDecodeError:
        print('Failed to load test ignore modules')
        sys.exit(1)


def read_stdlib_modules():
    try:
        response = requests.get(url=stdlib_modules_json_file)

        if response.status_code == 200:
            stdlib_modules_data = json.loads(response.text)
            read_dependency_data(stdlib_modules_data)
        else:
            print('Failed to access standard library dependency data from', stdlib_modules_json_file)
            sys.exit(1)

    except json.decoder.JSONDecodeError:
        print('Failed to load standard library dependency data')
        sys.exit(1)


def read_dependency_data(stdlib_modules_data):
    for module in stdlib_modules_data['standard_library']:
        name = module['name']
        level = module['level']
        version_key = module['version_key']
        if level < 9:
            stdlib_modules_by_level[level] = stdlib_modules_by_level.get(level, []) + [{"name": name,
                                                                                        "version_key": version_key}]


def clone_repositories():
    global exit_code

    # Clone ballerina-lang repo
    exit_code = os.system(f"git clone https://github.com/{github_user}/ballerina-lang.git || " +
                          "echo 'Please fork ballerina-lang repository to your github account'")
    if exit_code != 0:
        sys.exit(1)

    # Change branch
    exit_code = os.system(f"cd ballerina-lang;git checkout {ballerina_lang_branch}")
    os.system("cd ballerina-lang;git status")
    if exit_code != 0:
        sys.exit(1)

    # Clone standard library repos
    for level in stdlib_modules_by_level:
        stdlib_modules = stdlib_modules_by_level[level]
        for module in stdlib_modules:
            exit_code = os.system(f"git clone {constants.BALLERINA_ORG_URL}{module['name']}.git")
            if exit_code != 0:
                sys.exit(1)

    # Clone ballerina-distribution repo
    exit_code = os.system(f"git clone {constants.BALLERINA_ORG_URL}ballerina-distribution.git")
    if exit_code != 0:
        sys.exit(1)

    # Change branch
    exit_code = os.system(f"cd ballerina-distribution;git checkout {dist_repo_patch_branch}")
    os.system("cd ballerina-distribution;git status")
    if exit_code != 0:
        sys.exit(1)


def build_stdlib_repositories(enable_tests):
    global exit_code
    level_failed = False
    failed_modules = []

    cmd_exclude_tests = ''
    if enable_tests == 'false':
        cmd_exclude_tests = ' -x test'
        print("Tests are disabled")
    else:
        print("Tests are enabled")

    # Build ballerina-lang repo
    exit_code = os.system(f"cd ballerina-lang;" +
                          f"./gradlew clean build -x test publishToMavenLocal --stacktrace --scan")
    if exit_code != 0:
        failed_modules.append("ballerina-lang")
        write_failed_modules(failed_modules)
        sys.exit(1)

    # Build standard library repos
    for level in stdlib_modules_by_level:
        stdlib_modules = stdlib_modules_by_level[level]
        for module in stdlib_modules:
            os.system(f"echo Building Standard Library Module: {module['name']}")
            if module['name'] in build_ignore_modules:
                os.system(f"echo Skipped Building Standard Library Module: {module['name']}")
                continue

            elif module['name'] in test_ignore_modules:
                exit_code = os.system(f"cd {module['name']};" +
                                      f"export packageUser={ballerina_bot_username};" +
                                      f"export packagePAT={ballerina_bot_token};" +
                                      f"./gradlew clean build -x test publishToMavenLocal --stacktrace --scan " +
                                      "--console=plain --no-daemon --continue")
            else:
                exit_code = os.system(f"cd {module['name']};" +
                                      f"export packageUser={ballerina_bot_username};" +
                                      f"export packagePAT={ballerina_bot_token};" +
                                      f"./gradlew clean build{cmd_exclude_tests} publishToMavenLocal --stacktrace " +
                                      "--scan --console=plain --no-daemon --continue")

            if exit_code != 0:
                level_failed = True
                failed_modules.append(module['name'])

        if level_failed:
            write_failed_modules(failed_modules)
            sys.exit(1)

    # Build ballerina-distribution repo
    os.system("echo Building ballerina-distribution")
    exit_code = os.system(f"cd ballerina-distribution;" +
                          f"export packageUser={ballerina_bot_username};" +
                          f"export packagePAT={ballerina_bot_token};" +
                          f"./gradlew clean build{cmd_exclude_tests} " +
                          f"publishToMavenLocal --stacktrace --scan --console=plain --no-daemon --continue")
    if exit_code != 0:
        failed_modules.append("ballerina-distribution")
        write_failed_modules(failed_modules)
        sys.exit(1)


def change_version_to_snapshot():
    # Read ballerina-lang version
    lang_version = ""
    with open("ballerina-lang/gradle.properties", 'r') as config_file:
        for line in config_file:
            try:
                name, value = line.split("=")
                if name == "version":
                    lang_version = value
                    break
            except ValueError:
                continue
        config_file.close()

    print("Lang Version:", lang_version)

    # Change ballerina-lang version in the stdlib modules
    for level in stdlib_modules_by_level:
        stdlib_modules = stdlib_modules_by_level[level]
        for module in stdlib_modules:
            try:
                properties = dict()
                with open(f"{module['name']}/gradle.properties", 'r') as config_file:
                    for line in config_file:
                        try:
                            name, value = line.split("=")
                            if "ballerinaLangVersion" in name:
                                value = lang_version
                            properties[name] = value
                        except ValueError:
                            continue
                    config_file.close()
                # Increase java heap size for c2c module
                if module['name'] == "module-ballerina-c2c":
                    properties["org.gradle.jvmargs"] = "-Xmx4096m"

                with open(f"{module['name']}/gradle.properties", 'w') as config_file:
                    for prop in properties:
                        config_file.write(prop + "=" + properties[prop])
                    config_file.close()

            except FileNotFoundError:
                print(f"Cannot find the gradle.properties file for {module['name']}")
                sys.exit(1)

    # Change ballerina-lang version in ballerina-distribution
    properties = dict()
    with open("ballerina-distribution/gradle.properties", 'r') as config_file:
        for line in config_file:
            try:
                name, value = line.split("=")
                if "ballerinaLangVersion" in name:
                    value = lang_version
                properties[name] = value
            except ValueError:
                continue
        config_file.close()

    with open("ballerina-distribution/gradle.properties", 'w') as config_file:
        for prop in properties:
            config_file.write(prop + "=" + properties[prop])
        config_file.close()


def switch_to_branches_from_updated_stages():
    global exit_code

    properties = dict()

    with open("ballerina-distribution/gradle.properties", 'r') as config_file:
        for line in config_file:
            try:
                name, value = line.split("=")
                properties[name] = value
            except ValueError:
                continue
        config_file.close()

    # Checkout for new branches with last commit id
    for level in stdlib_modules_by_level:
        stdlib_modules = stdlib_modules_by_level[level]
        for module in stdlib_modules:
            if dist_repo_patch_branch != "master":
                if module['name'] == "module-ballerinai-transaction" and dist_repo_patch_branch == "2201.0.x":
                    os.system(f"echo {module['name']}")
                    exit_code = os.system(f"cd {module['name']};git checkout 1.0.x")

                    if exit_code != 0:
                        print(f"Failed to switch to branch '1.0.x' from last updated commit id for " +
                              f"{module['name']}")
                        sys.exit(1)
                    continue
                elif module['name'] == "module-ballerina-websubhub" and dist_repo_patch_branch == "2201.0.x":
                    os.system(f"echo {module['name']}")
                    exit_code = os.system(f"cd {module['name']};git checkout 2201.0.x")

                    if exit_code != 0:
                        print(f"Failed to switch to branch '2201.0.x' from last updated commit id for " +
                              f"{module['name']}")
                        sys.exit(1)
                    continue
                elif module['name'] == "module-ballerina-mime" and dist_repo_patch_branch == "2201.1.x":
                    os.system(f"echo {module['name']}")
                    exit_code = os.system(f"cd {module['name']};git checkout 2201.1.x")

                    if exit_code != 0:
                        print(f"Failed to switch to branch '2201.1.x' from last updated commit id for " +
                              f"{module['name']}")
                        sys.exit(1)
                    continue
                elif module['name'] == "module-ballerina-http" and dist_repo_patch_branch == "2201.1.x":
                    os.system(f"echo {module['name']}")
                    exit_code = os.system(f"cd {module['name']};git checkout 2201.1.x")

                    if exit_code != 0:
                        print(f"Failed to switch to branch '2201.1.x' from last updated commit id for " +
                              f"{module['name']}")
                        sys.exit(1)
                    continue
                elif module['name'] == "module-ballerina-c2c":
                    os.system(f"echo {module['name']}")
                    exit_code = os.system(f"cd {module['name']};git checkout {dist_repo_patch_branch}")

                    continue
                try:
                    version = properties[module['version_key']]
                    if len(version.split("-")) > 1:
                        updated_commit_id = version.split("-")[-1]
                        os.system(f"echo {module['name']}")
                        exit_code = os.system(f"cd {module['name']};git checkout -b full-build {updated_commit_id}")

                        if exit_code != 0:
                            print(f"Failed to create new branch from last updated commit id '{updated_commit_id}' for " +
                                  f"{module['name']}")
                            sys.exit(1)
                    else:
                        os.system(f"echo {module['name']}")
                        exit_code = os.system(f"cd {module['name']};git checkout v{version}")

                        if exit_code != 0:
                            print(f"Failed to switch to branch 'v{version}' from last updated commit id for " +
                                  f"{module['name']}")
                            sys.exit(1)

                except KeyError:
                    continue


def write_failed_modules(failed_module_names):
    with open("failed_modules.txt", "w") as file:
        for module_name in failed_module_names:
            file.write(module_name + "\n")
            print(f"Build failed for {module_name}")
        file.close()


main()
