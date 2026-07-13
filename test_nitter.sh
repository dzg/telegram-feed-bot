instances=(
    "https://nitter.net"
    "https://nitter.cz"
    "https://nitter.privacydev.net"
    "https://nitter.projectsegfau.lt"
    "https://nitter.poast.org"
)

for url in "${instances[@]}"; do
    echo "Testing $url..."
    status=$(curl -s -o /dev/null -w "%{http_code}" "$url/shanaka86/rss")
    echo "Status: $status"
done
