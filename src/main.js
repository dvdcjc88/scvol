import './style.css'

// Initialize the application
document.addEventListener('DOMContentLoaded', function() {
  console.log('SCVOL App Initialized');
  
  // Smooth scrolling for navigation links
  const navLinks = document.querySelectorAll('nav a[href^="#"]');
  
  navLinks.forEach(link => {
    link.addEventListener('click', function(e) {
      e.preventDefault();
      
      const targetId = this.getAttribute('href');
      const targetSection = document.querySelector(targetId);
      
      if (targetSection) {
        targetSection.scrollIntoView({
          behavior: 'smooth'
        });
      }
    });
  });
  
  // CTA Button functionality
  const ctaButton = document.querySelector('.cta-button');
  if (ctaButton) {
    ctaButton.addEventListener('click', function() {
      document.querySelector('#volunteers').scrollIntoView({
        behavior: 'smooth'
      });
    });
  }
  
  // Interactive volunteer cards
  const volunteerCards = document.querySelectorAll('.volunteer-card');
  volunteerCards.forEach(card => {
    const button = card.querySelector('.btn-secondary');
    if (button) {
      button.addEventListener('click', function() {
        alert('Thank you for your interest! Please contact us to learn more about this volunteer opportunity.');
      });
    }
  });
  
  // Mobile menu toggle (for responsive design)
  const navToggle = document.createElement('button');
  navToggle.className = 'nav-toggle';
  navToggle.innerHTML = '☰';
  navToggle.setAttribute('aria-label', 'Toggle navigation menu');
  
  const navbar = document.querySelector('.navbar');
  const navMenu = document.querySelector('.nav-menu');
  
  if (navbar && navMenu) {
    navbar.insertBefore(navToggle, navMenu);
    
    navToggle.addEventListener('click', function() {
      navMenu.classList.toggle('nav-menu-active');
      this.classList.toggle('nav-toggle-active');
    });
  }
  
  // Add animation on scroll
  const observerOptions = {
    threshold: 0.1,
    rootMargin: '0px 0px -50px 0px'
  };
  
  const observer = new IntersectionObserver(function(entries) {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('animate-in');
      }
    });
  }, observerOptions);
  
  // Observe all sections for animation
  const sections = document.querySelectorAll('.section');
  sections.forEach(section => {
    observer.observe(section);
  });
});

// Export for module compatibility
export default {};