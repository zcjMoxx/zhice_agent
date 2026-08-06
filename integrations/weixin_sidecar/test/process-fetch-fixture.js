globalThis.fetch = async (url) => {
  const target = String(url);
  if (target.includes("get_bot_qrcode")) {
    return jsonResponse({
      qrcode: "process-test-qr",
      qrcode_img_content: "https://example.invalid/process-test-qr",
    });
  }
  if (target.includes("get_qrcode_status")) {
    return jsonResponse({
      status: "confirmed",
      bot_token: "process-test-token",
      ilink_bot_id: "process-test-bot",
      ilink_user_id: "process-test-user",
      baseurl: "https://ilinkai.weixin.qq.com",
    });
  }
  throw new Error("unexpected process test request");
};

function jsonResponse(payload) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}
