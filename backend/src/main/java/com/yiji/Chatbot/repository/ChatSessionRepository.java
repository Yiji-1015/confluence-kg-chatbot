package com.yiji.Chatbot.repository;

import com.yiji.Chatbot.entity.ChatSession;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;

/**
 * 대화방(세션) JPA Repository 인터페이스
 */
@Repository
public interface ChatSessionRepository extends JpaRepository<ChatSession, String> {

    /**
     * 특정 사용자의 대화방 목록을 마지막 대화 시각(updatedAt) 최신순으로 정렬 조회
     */
    List<ChatSession> findAllByUserIdOrderByUpdatedAtDesc(String userId);

    /**
     * 전체 대화방 목록을 마지막 대화 시각(updatedAt) 최신순으로 정렬 조회
     * - 좌측 사이드바의 대화방 목록 표시에 사용됩니다.
     */
    List<ChatSession> findAllByOrderByUpdatedAtDesc();
}
