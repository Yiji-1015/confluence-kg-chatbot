package com.yiji.Chatbot.config;

import com.sun.net.httpserver.HttpServer;
import com.yiji.Chatbot.dto.InternalChatDto;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.web.client.RestClient;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * AI 엔진으로 나가는 요청이 본문을 싣고 나가는지 확인한다.
 *
 * 이게 깨진 적이 있다. 전송 구현을 Boot 4 기본(JDK HttpClient)에 맡겼더니 HTTP/2가 기본이라
 * 평문 연결에서 h2c 업그레이드를 시도했고, HTTP/1.1 전용인 uvicorn이 그걸 거절하면서
 * 본문이 사라졌다. 직렬화도 URL도 멀쩡한데 서버는 "본문 없음"을 받는 형태라 원인을 찾기 어렵다.
 * spring.http.clients.imperative.factory=simple 로 고정한 것을 여기서 붙잡아 둔다.
 */
@SpringBootTest
class AiEngineRequestTransportTest {

    private static final AtomicReference<String> CAPTURED_BODY = new AtomicReference<>("");
    private static final AtomicReference<List<String>> CAPTURED_HEADER_NAMES = new AtomicReference<>(List.of());
    private static HttpServer server;

    @DynamicPropertySource
    static void aiEngineStub(DynamicPropertyRegistry registry) {
        try {
            server = HttpServer.create(new InetSocketAddress(0), 0);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        server.createContext("/internal/chat", exchange -> {
            CAPTURED_HEADER_NAMES.set(List.copyOf(exchange.getRequestHeaders().keySet()));
            CAPTURED_BODY.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            byte[] reply = "{\"sessionId\":\"s-1\",\"answer\":\"ok\",\"sources\":[]}".getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, reply.length);
            exchange.getResponseBody().write(reply);
            exchange.close();
        });
        server.start();
        registry.add("ai-engine.base-url", () -> "http://localhost:" + server.getAddress().getPort());
    }

    @AfterAll
    static void stopServer() {
        if (server != null) {
            server.stop(0);
        }
    }

    @Autowired
    private RestClient aiEngineRestClient;

    @Test
    @DisplayName("요청 본문이 실제로 AI 엔진에 도착한다")
    void requestBodyReachesAiEngine() {
        InternalChatDto.Response response = aiEngineRestClient.post()
                .uri("/internal/chat")
                .contentType(MediaType.APPLICATION_JSON)
                .body(InternalChatDto.Request.builder()
                        .sessionId("s-1")
                        .query("질문")
                        .history(List.of())
                        .build())
                .retrieve()
                .body(InternalChatDto.Response.class);

        assertThat(CAPTURED_BODY.get())
                .contains("\"sessionId\":\"s-1\"")
                .contains("\"query\":\"질문\"");
        assertThat(response).isNotNull();
        assertThat(response.answer()).isEqualTo("ok");

        // h2c 업그레이드를 시도하지 않는 전송이어야 한다. JDK HttpClient는 HTTP/2가 기본이라
        // 평문 연결에서 Connection: Upgrade + HTTP2-Settings를 붙이고, uvicorn은 그걸 거절하면서
        // 본문을 잃는다. 이 두 헤더가 보이면 전송 구현이 다시 바뀐 것이다.
        assertThat(CAPTURED_HEADER_NAMES.get())
                .noneMatch(name -> name.equalsIgnoreCase("Upgrade"))
                .noneMatch(name -> name.equalsIgnoreCase("Http2-settings"));
    }
}
