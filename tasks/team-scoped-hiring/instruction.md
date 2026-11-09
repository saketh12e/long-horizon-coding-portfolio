# Team-scoped hiring workflow

A season-management service currently hires from one shared pool. Different teams can therefore vote for the same person, and the API can display options that the resolver later rejects.

Implement a team-specific hiring workflow. A team with a vacancy must receive exactly three durable candidates for that team and season, spanning premium, standard and developmental quality. Repeated reads before hiring must return the same candidate identities. Count only votes for that team’s candidates; choose the plurality winner, break ties by the lowest candidate identifier, and select the highest-rated candidate when no eligible vote exists. After hiring, attach the winner to the team, delete unused candidate records and return unused names to the name pool. The eligible-target API must expose the team’s candidates with their slot positions.

Preserve unrelated ballot types, general coach generation, economy rules and scoring behavior.
