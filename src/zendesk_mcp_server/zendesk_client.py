from typing import Dict, Any, List
import json
import os
from datetime import datetime, timezone

from zenpy import Zenpy
from zenpy.lib.api_objects import Comment


class ZendeskClient:
    def __init__(self, subdomain: str, email: str, token: str):
        """
        Initialize the Zendesk client using zenpy lib.
        """
        self.client = Zenpy(
            subdomain=subdomain,
            email=email,
            token=token
        )
        self.nameToIdsMap = self._load_name_to_ids_map()

    def _load_name_to_ids_map(self) -> Dict[str, int]:
        """
        Load the name to IDs mapping from the nameToIdsMap.js file.
        """
        try:
            # Get the directory where this file is located
            current_dir = os.path.dirname(os.path.abspath(__file__))
            map_file_path = os.path.join(current_dir, 'nameToIdsMap.js')
            
            with open(map_file_path, 'r') as file:
                content = file.read()
                
            # Extract the Map data from the JavaScript file
            # Remove the export and Map wrapper, then parse as JSON
            content = content.replace('export const nameToIdMap = new Map([', '[')
            content = content.replace(']);', ']')
            
            # Parse the array of arrays as JSON
            name_id_pairs = json.loads(content)
            
            # Convert to dictionary
            return {name: user_id for name, user_id in name_id_pairs}
            
        except Exception as e:
            print(f"Warning: Failed to load nameToIdsMap: {str(e)}")
            return {}

    def get_ticket(self, ticket_id: int) -> Dict[str, Any]:
        """
        Query a ticket by its ID and return all relevant information
        """
        try:
            ticket = self.client.tickets(id=ticket_id)
            return {
                'id': ticket.id,
                'subject': ticket.subject,
                'description': ticket.description,
                'status': ticket.status,
                'priority': ticket.priority,
                'created_at': str(ticket.created_at),
                'updated_at': str(ticket.updated_at),
                'requester_id': ticket.requester_id,
                'assignee_id': ticket.assignee_id,
                'organization_id': ticket.organization_id,
                'tags': ticket.tags
            }
        except Exception as e:
            raise Exception(f"Failed to get ticket {ticket_id}: {str(e)}")

    def get_ticket_comments(self, ticket_id: int) -> List[Dict[str, Any]]:
        """
        Get all comments for a specific ticket.
        """
        try:
            comments = self.client.tickets.comments(ticket=ticket_id)
            return [{
                'id': comment.id,
                'author_id': comment.author_id,
                'body': comment.body,
                'html_body': comment.html_body,
                'public': comment.public,
                'created_at': str(comment.created_at)
            } for comment in comments]
        except Exception as e:
            raise Exception(f"Failed to get comments for ticket {ticket_id}: {str(e)}")

    def post_comment(self, ticket_id: int, comment: str, public: bool = False, confirm_post: bool = False) -> str:
        """
        Post a comment to an existing ticket.
        """
        if not confirm_post:
            raise Exception("Permission denied: confirm_post must be True to post comment")
        
        try:
            ticket = self.client.tickets(id=ticket_id)
            ticket.comment = Comment(
                html_body=comment,
                public=public  
            )
            self.client.tickets.update(ticket)
            return comment
        except Exception as e:
            raise Exception(f"Failed to post comment on ticket {ticket_id}: {str(e)}")

    def get_tickets_by_agent(self, agent_identifier: str) -> List[Dict[str, Any]]:
        """
        Get all unsolved ticket IDs and statuses assigned to a specific agent by their first name, full name, or user ID.
        """
        try:
            # Check if the input is a numeric ID
            if agent_identifier.isdigit():
                # Handle as user ID
                assignee_id = int(agent_identifier)
                tickets = self.client.search(query=f'assignee:{assignee_id} status:open status:pending status:"Feature Request Review Pending" status:"ENG Confirmed Bug"')
                all_tickets = list(tickets)
            else:
                # Handle as name (first name or full name)
                # First try exact full name match
                if agent_identifier in self.nameToIdsMap:
                    # Full name match
                    assignee_id = self.nameToIdsMap[agent_identifier]
                    tickets = self.client.search(query=f'assignee:{assignee_id} status:open status:pending status:"Feature Request Review Pending" status:"ENG Confirmed Bug"')
                    all_tickets = list(tickets)
                else:
                    # Try first name match
                    first_name = agent_identifier.split()[0].lower()
                    
                    # Find all agents whose first name matches
                    matching_agent_ids = []
                    for full_name, user_id in self.nameToIdsMap.items():
                        agent_first_name = full_name.split()[0].lower()
                        if agent_first_name == first_name:
                            matching_agent_ids.append(user_id)
                    
                    if not matching_agent_ids:
                        raise Exception(f"No agent found with name or ID: {agent_identifier}")
                    
                    # Get all unsolved tickets assigned to any agent with this first name
                    all_tickets = []
                    for assignee_id in matching_agent_ids:
                        tickets = self.client.search(query=f'assignee:{assignee_id} status:open status:pending status:"Feature Request Review Pending" status:"ENG Confirmed Bug"')
                        all_tickets.extend(tickets)
            
            return [{'id': ticket.id, 'status': ticket.status} for ticket in all_tickets]
        except Exception as e:
            raise Exception(f"Failed to get tickets for agent {agent_identifier}: {str(e)}")

    def get_ticket_priority(self, ticket_id: int) -> int:
        """
        Calculate the priority of a ticket based on multiple factors:
        1. SLA enterprise tag (immediately higher priority)
        2. Age of the ticket
        3. Time since last response
        4. Status priority (Open > New > Pending > Feature Request Review Pending > ENG Confirmed Bug)
        
        Returns a dictionary with priority score and breakdown of factors.
        """
        try:
            # Get ticket details
            ticket = self.client.tickets(id=ticket_id)
            comments = self.client.tickets.comments(ticket=ticket_id)
            
            # higher priority_score = more urgent
            priority_score = 0
            
            if ticket.tags and 'sla_enterprise' in ticket.tags:
                priority_score += 100
            
            now = datetime.now(timezone.utc)
            
            # Ensure ticket.created_at is a datetime object
            ticket_created = ticket.created_at
            if isinstance(ticket_created, str):
                ticket_created = datetime.fromisoformat(ticket_created.replace('Z', '+00:00'))
            
            ticket_age_hours = (now - ticket_created).total_seconds() / 3600
            age_score = (ticket_age_hours / 24) * 5
            priority_score += age_score
            
            if comments:
                latest_comment = max(comments, key=lambda c: c.created_at)
                
                # Ensure comment.created_at is a datetime object
                comment_created = latest_comment.created_at
                if isinstance(comment_created, str):
                    comment_created = datetime.fromisoformat(comment_created.replace('Z', '+00:00'))
                
                hours_since_response = (now - comment_created).total_seconds() / 3600
                response_score = (hours_since_response / 24) * 10
                priority_score += response_score
            
            # Factor 4: Status priority
            status_scores = {
                'open': 100,
                'new': 75,
                'pending': 25,
                'feature request review pending': 0,
                'eng confirmed bug': 0
            }
            
            status_score = status_scores.get((ticket.status.lower()), 0)
            priority_score += status_score
            
            return int(priority_score)
            
        except Exception as e:
            raise Exception(f"Failed to calculate priority for ticket {ticket_id}: {str(e)}")
