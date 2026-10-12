pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
    }

    environment {
        BMC_URL = 'https://127.0.0.1:2443'
        BMC_HOST = 'https://127.0.0.1:2443'
        BMC_USER = 'root'
        BMC_PASSWORD = '0penBmc'
        BMC_VERIFY_TLS = '0'
        IPMI_HOST = '127.0.0.1'
        IPMI_PORT = '2623'
        OPENBMC_IMAGE = '/opt/openbmc/openbmc.mtd'
        CI_VENV = '.ci-venv'
    }

    stages {
        stage('Prepare environment') {
            steps {
                sh '''#!/usr/bin/env bash
                    set -euo pipefail
                    rm -rf "$CI_VENV" artifacts
                    mkdir -p artifacts/qemu artifacts/api artifacts/load artifacts/webui
                    python3 -m venv "$CI_VENV"
                    "$CI_VENV/bin/python" -m pip install --upgrade pip
                    "$CI_VENV/bin/python" -m pip install \
                        'pytest>=7.4,<10' \
                        'requests>=2.31,<3' \
                        'selenium>=4.20,<5' \
                        'locust>=2.31,<3'
                '''
            }
        }

        stage('Start OpenBMC in QEMU') {
            steps {
                sh '''#!/usr/bin/env bash
                    set -euo pipefail
                    test -f "$OPENBMC_IMAGE"
                    cp "$OPENBMC_IMAGE" artifacts/qemu/openbmc-ci.mtd

                    export JENKINS_NODE_COOKIE=dontKillMe
                    nohup qemu-system-arm \
                        -m 256 \
                        -M romulus-bmc \
                        -nographic \
                        -drive file=artifacts/qemu/openbmc-ci.mtd,format=raw,if=mtd \
                        -net nic \
                        -net user,hostfwd=tcp:127.0.0.1:2222-:22,hostfwd=tcp:127.0.0.1:2443-:443,hostfwd=tcp:127.0.0.1:8000-:80,hostfwd=udp:127.0.0.1:2623-:623,hostname=jenkins \
                        > artifacts/qemu/qemu.log 2>&1 &
                    echo $! > artifacts/qemu/qemu.pid

                    for attempt in $(seq 1 180); do
                        if curl --silent --show-error --insecure \
                            --user "$BMC_USER:$BMC_PASSWORD" \
                            "$BMC_URL/redfish/v1/" >/dev/null 2>&1; then
                            echo "OpenBMC is ready after ${attempt} checks" | tee artifacts/qemu/readiness.txt
                            exit 0
                        fi
                        sleep 2
                    done

                    echo 'OpenBMC did not become ready in 360 seconds' | tee artifacts/qemu/readiness.txt
                    exit 1
                '''
            }
        }

        stage('Redfish API tests') {
            steps {
                catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                    sh '''#!/usr/bin/env bash
                        set +e
                        IPMI_PASSWORD="$BMC_PASSWORD" \
                            "$CI_VENV/bin/python" -m pytest \
                            lab5/test_redfish.py \
                            -v --tb=short \
                            --junitxml=artifacts/api/junit.xml \
                            > artifacts/api/pytest.log 2>&1
                        status=$?
                        cat artifacts/api/pytest.log
                        exit "$status"
                    '''
                }
            }
            post {
                always {
                    junit testResults: 'artifacts/api/junit.xml', allowEmptyResults: true
                    archiveArtifacts artifacts: 'artifacts/api/**', allowEmptyArchive: true
                }
            }
        }

        stage('OpenBMC load test') {
            steps {
                catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                    sh '''#!/usr/bin/env bash
                        set +e
                        "$CI_VENV/bin/locust" \
                            -f lab6/locustfile.py \
                            --headless \
                            --users 10 \
                            --spawn-rate 2 \
                            --run-time 60s \
                            --csv artifacts/load/locust \
                            --html artifacts/load/report.html \
                            > artifacts/load/locust.log 2>&1
                        status=$?
                        cat artifacts/load/locust.log
                        exit "$status"
                    '''
                }
            }
            post {
                always {
                    archiveArtifacts artifacts: 'artifacts/load/**', allowEmptyArchive: true
                }
            }
        }

        stage('OpenBMC Web UI tests') {
            steps {
                catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                    sh '''#!/usr/bin/env bash
                        set +e
                        xvfb-run -a "$CI_VENV/bin/python" -m pytest \
                            lab4-selenium/openbmc_auth_tests.py \
                            -v --tb=short \
                            --junitxml=artifacts/webui/junit.xml \
                            > artifacts/webui/pytest.log 2>&1
                        status=$?
                        cat artifacts/webui/pytest.log
                        exit "$status"
                    '''
                }
            }
            post {
                always {
                    junit testResults: 'artifacts/webui/junit.xml', allowEmptyResults: true
                    archiveArtifacts artifacts: 'artifacts/webui/**', allowEmptyArchive: true
                }
            }
        }
    }

    post {
        always {
            sh '''#!/usr/bin/env bash
                set +e
                if [[ -f artifacts/qemu/qemu.pid ]]; then
                    kill "$(cat artifacts/qemu/qemu.pid)" 2>/dev/null || true
                    wait "$(cat artifacts/qemu/qemu.pid)" 2>/dev/null || true
                fi
                rm -f artifacts/qemu/openbmc-ci.mtd
            '''
            archiveArtifacts artifacts: 'artifacts/qemu/**', allowEmptyArchive: true
        }
    }
}
