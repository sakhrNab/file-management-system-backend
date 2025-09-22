#!/bin/bash
# Enhanced health check script that detects 503 errors and unreachable states

HEALTH_URL="http://localhost:8003/health"
MAX_RETRIES=3
RETRY_DELAY=2

# Function to check if service is reachable
check_reachability() {
    local url=$1
    local max_retries=$2
    local retry_delay=$3
    
    for ((i=1; i<=max_retries; i++)); do
        # Check if port is open using curl
        if ! curl -s --connect-timeout 2 http://localhost:8003 >/dev/null 2>&1; then
            echo "Service not accessible (attempt $i/$max_retries)"
            sleep $retry_delay
            continue
        fi
        
        # Check HTTP response
        local response=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 --max-time 10 "$url" 2>/dev/null)
        local curl_exit_code=$?
        
        if [ $curl_exit_code -eq 0 ]; then
            case $response in
                200)
                    echo "Service healthy (HTTP 200)"
                    return 0
                    ;;
                503)
                    echo "Service returning 503 Gateway Error (attempt $i/$max_retries)"
                    sleep $retry_delay
                    ;;
                502|504)
                    echo "Service returning $response Gateway Error (attempt $i/$max_retries)"
                    sleep $retry_delay
                    ;;
                *)
                    echo "Service returning unexpected status: $response (attempt $i/$max_retries)"
                    sleep $retry_delay
                    ;;
            esac
        else
            echo "Service unreachable - curl failed with exit code $curl_exit_code (attempt $i/$max_retries)"
            sleep $retry_delay
        fi
    done
    
    echo "Service unhealthy after $max_retries attempts"
    return 1
}

# Run the health check
check_reachability "$HEALTH_URL" $MAX_RETRIES $RETRY_DELAY
exit $?
