<?php
declare(strict_types=1);

header('Content-Type: application/json');
$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
if ($path === '/health') {
    echo '{"ok":true}';
    exit;
}

$urls = [
    '/direct' => 'https://upstream:8443/work',
    '/envoy' => 'http://envoy:8080/work',
];
if (!isset($urls[$path])) {
    http_response_code(404);
    echo '{"error":"Use /direct ou /envoy"}';
    exit;
}

// Um handle novo por request PHP, sem pool persistente na aplicação.
// As mesmas opções se aplicam aos dois caminhos; o pool fica no Envoy.
$curl = curl_init($urls[$path]);
curl_setopt_array($curl, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HTTP_VERSION => CURL_HTTP_VERSION_1_1,
    CURLOPT_CONNECTTIMEOUT_MS => 2000,
    CURLOPT_TIMEOUT_MS => 5000,
    CURLOPT_CAINFO => '/ca/ca.crt',
    CURLOPT_SSL_VERIFYPEER => true,
    CURLOPT_SSL_VERIFYHOST => 2,
    CURLOPT_NOPROXY => '*',
]);
$body = curl_exec($curl);
$status = (int) curl_getinfo($curl, CURLINFO_RESPONSE_CODE);
$error = curl_error($curl);
unset($curl); // Destrói o handle e fecha sua conexão ao terminar a chamada.

if ($body === false || $status !== 200) {
    http_response_code(502);
    echo json_encode(['error' => $error ?: "upstream HTTP $status"]);
    exit;
}
echo $body;
