import urllib.request
import xml.etree.ElementTree as ET

url = "https://rss.app/feeds/qiH9ahYUE3qyOnOj.xml"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req) as response:
        xml_data = response.read()
    root = ET.fromstring(xml_data)
    for item in root.findall('.//item')[:3]:
        link = item.find('link').text
        pubDate = item.find('pubDate').text
        print(f"Found: {link} at {pubDate}")
except Exception as e:
    print(f"Error: {e}")
