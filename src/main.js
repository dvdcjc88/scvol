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
  
  // Volunteer opportunity modal
  const modal = document.getElementById('volunteer-modal');
  const modalTitle = modal.querySelector('.modal-title');
  const modalBody = modal.querySelector('.modal-body');
  const modalOverlay = modal.querySelector('.modal-overlay');
  const modalClose = modal.querySelector('.modal-close');
  const modalCta = modal.querySelector('.modal-cta');

  const opportunityDetails = {
    'Community Outreach': 'Help us reach underserved communities by organizing events, delivering resources, and building lasting relationships with local families and organizations.',
    'Environmental Conservation': 'Work alongside our team to clean up green spaces, plant trees, monitor local ecosystems, and raise environmental awareness in our neighborhoods.',
    'Education Support': 'Tutor students, mentor young learners, and assist teachers in local schools and after-school programs to help every child reach their potential.',
  };

  function openModal(title) {
    modalTitle.textContent = title;
    modalBody.textContent = opportunityDetails[title] || 'Thank you for your interest! We\'d love to tell you more about this opportunity.';
    modal.classList.add('modal-open');
    document.body.style.overflow = 'hidden';
    modalClose.focus();
  }

  function closeModal() {
    modal.classList.remove('modal-open');
    document.body.style.overflow = '';
  }

  const volunteerCards = document.querySelectorAll('.volunteer-card');
  volunteerCards.forEach(card => {
    const button = card.querySelector('.btn-secondary');
    if (button) {
      button.addEventListener('click', function() {
        const title = card.querySelector('h3').textContent;
        openModal(title);
      });
    }
  });

  modalOverlay.addEventListener('click', closeModal);
  modalClose.addEventListener('click', closeModal);
  modalCta.addEventListener('click', function() {
    closeModal();
    document.querySelector('#contact').scrollIntoView({ behavior: 'smooth' });
  });
  modal.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeModal();
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