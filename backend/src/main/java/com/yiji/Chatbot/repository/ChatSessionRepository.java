package com.yiji.Chatbot.repository;

import com.yiji.Chatbot.entity.ChatSession;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

/**
 * 대화방(세션) JPA Repository
 */
@Repository
public interface ChatSessionRepository extends JpaRepository<ChatSession, String> {

    /**
     * 특정 사용자의 대화방 목록. 정렬과 크기는 Pageable이 정한다.
     *
     * 예전에는 `findAllByUserIdOrderByUpdatedAtDesc`로 전부 반환했다. 대화방이 늘어날수록
     * 응답이 무한정 커지고, 사이드바는 어차피 최근 것만 보여준다.
     */
    Page<ChatSession> findByUserId(String userId, Pageable pageable);
}
